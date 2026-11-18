# Disclaimer

Read this before you connect a real account.

- **This is not financial advice.** It is software and a record of what its author tested. Nobody here knows your finances, your taxes or your risk limits.
- **It can lose money.** Options and stocks can fall fast. A covered call does not protect you from a drop in the stock. A stock bought by the rotation rule can fall right after you buy it.
- **The tests are history, not a promise.** They cover January 2024 to October 2026, a period when the market mostly rose. Nothing here has been tested in a bear market.
- **One rule is beta.** The rotation rule (buy the strongest-trending stock with free cash) has only partial test results. It is labelled beta on every trade it proposes. See `RESULTS.md`.
- **Fills will differ from the tests.** The tests assume prices and spreads that your real orders will not match exactly.
- **You are responsible for every order.** The engine proposes. You approve. With Robinhood, the Robinhood app asks you again. With Alpaca there is no second prompt: the Approve button on the dashboard is the approval.
- **Taxes are not modelled.** Premium and call-aways are usually short-term gains. A call with 30 days or fewer to expiry can affect the holding period of your shares. Talk to a tax professional.
- **The Robinhood order path is new.** It was written from Robinhood's documented responses and has not been run end to end from this kit. Start with one small order, with trade approvals on.
- **Keep the dashboard private.** It can queue orders. Run it on your own computer, or on a server behind the password the installer sets. Never put it on the open internet without one.
- **Start in practice mode, then paper.** Practice mode uses a made-up account. Alpaca paper trading uses fake money. Use both before real money.

The software is provided as is, with no warranty. See `LICENSE`.
