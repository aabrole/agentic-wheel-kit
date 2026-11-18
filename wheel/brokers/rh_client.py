"""(From github.com/aabrole/agentic-trading-kit, MIT, with the option tools added.)
Robinhood agent connection: OAuth (DCR + PKCE) handshake, token storage, MCP session.

The connect flow mirrors lvl.teach.trade: we register our own OAuth client, send the user
to Robinhood (sign-in, agent-account onboarding/KYC, consent), Robinhood redirects to
http://localhost:8765/callback which shows a dead page, and the user pastes that address
back into our page. The server validates state and exchanges the code with the saved PKCE
verifier. Tokens never leave this machine.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx2
from mcp import ClientSession
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('WHEEL_STATE_DIR', ROOT.parent.parent / 'state')) / 'robinhood'
ENDPOINT = 'https://agent.robinhood.com/mcp/trading'
REDIRECT = 'http://localhost:8765/callback'
CLIENT_NAME = 'Agentic Wheel Kit'

READ_TOOLS = frozenset({'get_accounts', 'get_portfolio', 'get_equity_positions', 'get_equity_orders',
                        'get_equity_quotes', 'get_trade_approval_setting', 'get_trade_approvals',
                        'get_option_positions', 'get_option_chains', 'get_option_instruments',
                        'get_option_quotes', 'get_option_orders', 'get_earnings_results'})
ORDER_TOOLS = frozenset({'review_equity_order', 'place_equity_order', 'review_option_order', 'place_option_order'})


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(name, default=None):
    p = STATE / name
    return json.loads(p.read_text()) if p.exists() else default


def set_status(status, **extra):
    atomic_json(STATE / 'connection.json', {'status': status, 'updated_at': now(), **extra})


class Storage:
    async def get_tokens(self):
        p = STATE / 'tokens.json'
        return OAuthToken.model_validate_json(p.read_text()) if p.exists() else None

    async def set_tokens(self, value):
        atomic_json(STATE / 'tokens.json', value.model_dump(mode='json'))

    async def get_client_info(self):
        p = STATE / 'client.json'
        return OAuthClientInformationFull.model_validate_json(p.read_text()) if p.exists() else None

    async def set_client_info(self, value):
        atomic_json(STATE / 'client.json', value.model_dump(mode='json'))


class ReauthRequired(Exception):
    pass


async def _deny_redirect(url):
    raise ReauthRequired('Robinhood needs you to reconnect')


async def _deny_callback():
    raise ReauthRequired('Robinhood needs you to reconnect')


@asynccontextmanager
async def session(redirect_handler=_deny_redirect, callback_handler=_deny_callback):
    oauth = OAuthClientProvider(
        server_url=ENDPOINT,
        client_metadata=OAuthClientMetadata(
            client_name=CLIENT_NAME, redirect_uris=[AnyUrl(REDIRECT)],
            grant_types=['authorization_code', 'refresh_token'], response_types=['code'],
            token_endpoint_auth_method='none', scope='internal'),
        storage=Storage(), redirect_handler=redirect_handler, callback_handler=callback_handler)
    async with httpx2.AsyncClient(auth=oauth, timeout=httpx2.Timeout(30, read=60)) as client:
        async with streamable_http_client(ENDPOINT, http_client=client, terminate_on_close=False) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                yield s


def data_of(result):
    obj = result.structured_content
    if obj is None:
        for c in result.content:
            if getattr(c, 'type', '') == 'text':
                try:
                    obj = json.loads(c.text)
                    break
                except (TypeError, json.JSONDecodeError):
                    continue
    if result.is_error:
        raise RuntimeError(f'Broker call failed: {obj if obj is not None else result.content}')
    if not isinstance(obj, dict) or not isinstance(obj.get('data'), dict):
        raise ValueError('Unexpected broker response')
    return obj['data']


async def call(s, name, args, allow_orders=False):
    if name not in READ_TOOLS and not (allow_orders and name in ORDER_TOOLS):
        raise PermissionError(f'{name} is not allowed')
    return data_of(await s.call_tool(name, args))


async def pages(s, name, args, key):
    rows, seen = [], set()
    for _ in range(50):
        d = await call(s, name, args)
        rows.extend(x for x in d.get(key) or [] if x)
        cur = d.get('next')
        if not cur:
            return rows
        if cur in seen:
            raise ValueError('Repeated pagination cursor')
        seen.add(cur)
        args = {**args, 'cursor': cur}
    raise ValueError('Pagination limit reached')


def agent_account(accounts):
    """The one account this agent may trade. Never falls back to anything else."""
    tradable = [a for a in accounts if a.get('agentic_allowed') is True and a.get('state') == 'active'
                and not a.get('deactivated') and not a.get('permanently_deactivated')]
    return tradable[0] if len(tradable) == 1 else None


def bound_account(accounts):
    """The account saved at connect time, still agent-tradable."""
    saved = read_json('account.json')
    a = agent_account(accounts)
    if not saved or not a or a['account_number'] != saved['account_number']:
        raise PermissionError('Connected account missing or changed; reconnect from the dashboard')
    return a


class Handshake:
    """One in-flight connect attempt, driven by two web requests (start, finish)."""

    def __init__(self):
        self.url = None
        self.expected_state = None
        self.url_ready = asyncio.Event()
        self.code = asyncio.get_running_loop().create_future()
        self.task = None
        self.error = None

    async def _redirect(self, url):
        self.url = url
        self.expected_state = parse_qs(urlparse(url).query).get('state', [None])[0]
        self.url_ready.set()

    async def _callback(self):
        return await asyncio.wait_for(self.code, timeout=1800)

    def submit(self, pasted):
        """Validate the pasted localhost address. Returns an error string or None."""
        try:
            u = urlparse(pasted.strip())
        except ValueError:
            return 'bad_url'
        q = parse_qs(u.query)
        if not q.get('state') or not (q.get('code') or q.get('error')):
            return 'robinhood_page' if (u.hostname or '').endswith('robinhood.com') else 'missing_code'
        if q['state'][0] != self.expected_state:
            return 'state_mismatch'  # checked before anything, including error callbacks
        if self.code.done():
            return 'already_submitted'
        if q.get('error'):
            self.code.set_exception(ReauthRequired(q.get('error_description', q['error'])[0]))
            return 'robinhood_error'
        self.code.set_result(AuthorizationCodeResult(code=q['code'][0], state=q['state'][0],
                                                     iss=q.get('iss', [None])[0]))
        return None

    async def run(self):
        """Authorize (registering our client on first use), then bind the agent account.
        Retries reuse the same client registration so Robinhood sees one agent."""
        (STATE / 'tokens.json').unlink(missing_ok=True)
        set_status('awaiting_consent')
        try:
            async with session(self._redirect, self._callback) as s:
                accounts = (await call(s, 'get_accounts', {}))['accounts']
                a = agent_account(accounts)
                if not a:
                    set_status('no_agentic_account')
                    return
                approval = (await call(s, 'get_trade_approval_setting',
                                       {'account_number': a['account_number']}))['setting']
                atomic_json(STATE / 'account.json', {
                    'account_number': a['account_number'], 'nickname': a.get('nickname'),
                    'type': a.get('type'), 'option_level': a.get('option_level'),
                    'human_must_approve_trades': approval.get('human_must_approve_trades'),
                    'bound_at': now()})
                set_status('connected', last4=a['account_number'][-4:], nickname=a.get('nickname'))
        except Exception as e:  # surfaced on the dashboard; tokens are never logged
            self.error = type(e).__name__
            set_status('connect_failed', error=f'{type(e).__name__}: {str(e)[:200]}')
        finally:
            self.url_ready.set()
