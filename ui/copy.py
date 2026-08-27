"""Plain-English explanations for every input and greek shown in the UI.

Single source of copy so wording stays consistent across sliders, expander
text, and cards -- assume the reader has never taken a finance class.
"""

INPUTS = {
    "spot": "What the stock costs right now, per share.",
    "strike": "The price you'd be locked into buying (or selling) the stock "
    "at, if you use this option.",
    "rate": "The 'safe' interest rate you could otherwise earn on cash, "
    "like from a government bond. Higher rates make call options worth a "
    "little more and put options worth a little less.",
    "vol": "How much the stock's price tends to jump around. Higher "
    "volatility means the stock is less predictable, which makes any "
    "option on it more valuable -- there's more of a chance it swings "
    "your way.",
    "time": "How long until this option expires. More time means more "
    "chances for the stock to move in your favor, so the option is worth "
    "more.",
    "option_type": "A call is the right to buy the stock at the strike "
    "price. A put is the right to sell it at the strike price.",
}

GREEKS = {
    "delta": "If the stock price moves $1, this option's price moves about "
    "this many dollars.",
    "gamma": "How fast delta itself changes as the stock price moves -- a "
    "measure of how quickly your exposure shifts.",
    "vega": "If volatility (how jumpy the stock is) rises by 1 percentage "
    "point, this is roughly how much the option's price changes.",
    "theta": "How much value this option loses each year just from time "
    "passing, even if nothing else changes. Options are a 'melting ice "
    "cube' -- this is the melt rate.",
    "rho": "If interest rates rise by 1 percentage point, this is roughly "
    "how much the option's price changes.",
}
