"""Streamlit web UI for the options pricing engine.

Run with:
    streamlit run app.py

Wraps the existing pricers (pricers/black_scholes.py, binomial.py,
monte_carlo.py) directly -- no pricing logic lives in this file.
"""

import streamlit as st

from pricers import binomial
from pricers import black_scholes as bs
from pricers import monte_carlo as mc
from pricers.common import OptionParams
from ui import background, charts, copy, videos

# Lighter than compare.py's CLI defaults (500 steps / 500k paths) so the
# interactive sliders stay snappy; still visually convergent.
BINOMIAL_STEPS = 150
MC_PATHS = 50_000
MC_SEED = 42

st.set_page_config(page_title="Options Pricing", layout="centered")

st.markdown(
    """
    <style>
    #MainMenu, footer, header {visibility: hidden;}
    .block-container {padding-top: 3rem; padding-bottom: 4rem; max-width: 680px;}
    h1 {font-weight: 600; letter-spacing: -0.02em; margin-bottom: 0.2rem;}
    div[data-testid="stMetricValue"] {font-size: 2.6rem; font-weight: 600;}
    div[data-testid="stMetric"] {text-align: center;}
    hr {margin: 2.2rem 0;}

    /* Streamlit paints an opaque white background on both <body> and its
       own [data-testid="stApp"] wrapper. Both sit *above* our z-index:-1
       background iframe in the stacking order, so left as-is they fully
       hide it -- the canvas draws correctly underneath, it's just covered.
       Making these two transparent is what actually lets it show through. */
    body, [data-testid="stApp"], [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        background: transparent !important;
    }

    /* The content column then gets its own frosted panel so text stays
       calm and high-contrast, with the ambient motion visible around and
       through its translucent edges rather than directly behind the text. */
    /* No backdrop-filter here on purpose: filter/backdrop-filter on an
       ancestor establishes a new CSS containing block for position:fixed
       descendants, which would hijack the background iframe below into
       anchoring to this panel instead of the viewport. A solid
       (non-blurred) translucent background keeps text crisp without that
       side effect. */
    [data-testid="stMainBlockContainer"] {
        background: rgba(255, 255, 255, 0.94);
        border-radius: 28px;
        padding-left: 3rem;
        padding-right: 3rem;
        margin-top: 1.5rem;
        margin-bottom: 1.5rem;
    }

    /* Ambient background component: stretch it to a fixed full-viewport
       layer behind everything else (see ui/background.py). It's the only
       st.iframe in this app, so this selector is unambiguous.

       z-index here was chosen empirically via headless-browser inspection,
       not by theory: z-index -1 and 0 both drew the canvas correctly (
       confirmed via its own pixel data) but the iframe never actually
       composited as visible on screen -- some low/negative z-index
       iframes don't reliably layer correctly in this Chromium/Streamlit
       combination. A clearly positive value composites correctly and
       still stays below every other app element, all of which sit at
       z-index >= 999990 (Streamlit's own header/sidebar) or paint after
       this component in DOM order (everything else in the main content
       column, since render_background() runs first in app.py). */
    [data-testid="stIFrame"] {
        position: fixed !important;
        inset: 0 !important;
        width: 100vw !important;
        height: 100vh !important;
        z-index: 1 !important;
        border: none !important;
        pointer-events: auto;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

background.render_background()

st.title("What's this option worth?")
st.caption(
    "Adjust the numbers on the left and watch the price update. "
    "Hover the (?) next to any label for a plain-English explanation."
)

with st.sidebar:
    st.header("Option details")
    spot = st.slider(
        "Stock price", min_value=1.0, max_value=500.0, value=100.0, step=1.0,
        help=copy.INPUTS["spot"],
    )
    strike = st.slider(
        "Strike price", min_value=1.0, max_value=500.0, value=100.0, step=1.0,
        help=copy.INPUTS["strike"],
    )
    rate = st.slider(
        "Interest rate", min_value=0.0, max_value=0.15, value=0.05, step=0.005,
        format="%.1f%%", help=copy.INPUTS["rate"],
    )
    vol = st.slider(
        "Volatility", min_value=0.01, max_value=1.0, value=0.2, step=0.01,
        format="%.0f%%", help=copy.INPUTS["vol"],
    )
    time = st.slider(
        "Time to expiry (years)", min_value=0.05, max_value=3.0, value=1.0, step=0.05,
        help=copy.INPUTS["time"],
    )
    option_type = st.radio(
        "Option type",
        options=["call", "put"],
        format_func=lambda t: "Call (right to buy)" if t == "call" else "Put (right to sell)",
        horizontal=True,
        help=copy.INPUTS["option_type"],
    )

params = OptionParams(spot=spot, strike=strike, rate=rate, vol=vol, time=time, option_type=option_type)

bs_price = bs.price(params)
bs_greeks = bs.greeks(params)
euro_tree_price = binomial.price_european(params, steps=BINOMIAL_STEPS)
american_tree_price = binomial.price_american(params, steps=BINOMIAL_STEPS)
mc_result = mc.price(params, n_paths=MC_PATHS, seed=MC_SEED)

st.metric("Price", f"${bs_price:,.2f}")
st.caption(
    f"<div style='text-align:center'>This option would cost about "
    f"<b>${bs_price:,.2f}</b> today.</div>",
    unsafe_allow_html=True,
)

st.divider()

st.subheader("Three ways of calculating this")
st.caption(
    "Three completely different methods, cross-checked against each other. "
    "They agree, which is a good sign the price is right."
)
col1, col2, col3 = st.columns(3)
col1.metric("Black-Scholes", f"${bs_price:,.2f}")
col2.metric("Binomial tree", f"${euro_tree_price:,.2f}")
col3.metric("Monte Carlo", f"${mc_result.price:,.2f}")

for col, key in zip((col1, col2, col3), ("black_scholes", "binomial", "monte_carlo")):
    video = videos.VIDEOS[key]
    with col.expander("▶ Watch a 5-min explanation"):
        st.caption(f"{video['title']} — {video['channel']}")
        st.markdown(videos.embed_html(video["youtube_id"]), unsafe_allow_html=True)

st.caption(
    f"Monte Carlo's own estimate of its uncertainty (95% confidence "
    f"interval): ${mc_result.ci_low:,.2f} - ${mc_result.ci_high:,.2f}"
)
st.caption(
    f"If early exercise were allowed (an \"American\" option), this would "
    f"be worth ${american_tree_price:,.2f} instead."
)

st.divider()

with st.expander("What affects this price?"):
    st.markdown(f"**Delta: {bs_greeks.delta:.2f}** — {copy.GREEKS['delta']}")
    st.markdown(f"**Gamma: {bs_greeks.gamma:.4f}** — {copy.GREEKS['gamma']}")
    st.markdown(f"**Vega: {bs_greeks.vega:.2f}** — {copy.GREEKS['vega']}")
    st.markdown(f"**Theta: {bs_greeks.theta:.2f}** — {copy.GREEKS['theta']}")
    st.markdown(f"**Rho: {bs_greeks.rho:.2f}** — {copy.GREEKS['rho']}")

with st.expander("How was this calculated?"):
    st.write(
        "The binomial tree breaks time into small steps and works out the "
        "price by looking ahead; the more steps, the closer it gets to the "
        "Black-Scholes answer (the dashed line)."
    )
    st.pyplot(charts.binomial_convergence_figure(params, bs_price, BINOMIAL_STEPS))
    st.write(
        "Monte Carlo simulates thousands of random possible futures for "
        "the stock and averages the results; the more simulations, the "
        "tighter its confidence band gets around the true price."
    )
    st.pyplot(charts.monte_carlo_convergence_figure(params, bs_price, MC_PATHS, MC_SEED))
