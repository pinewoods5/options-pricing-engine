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
from ui import charts

# Lighter than compare.py's CLI defaults (500 steps / 500k paths) so the
# interactive sliders stay snappy; still visually convergent.
BINOMIAL_STEPS = 150
MC_PATHS = 50_000
MC_SEED = 42

st.set_page_config(page_title="Options Pricing", layout="centered")

st.title("Options Pricing")

with st.sidebar:
    st.header("Option details")
    spot = st.slider("Spot price", min_value=1.0, max_value=500.0, value=100.0, step=1.0)
    strike = st.slider("Strike price", min_value=1.0, max_value=500.0, value=100.0, step=1.0)
    rate = st.slider("Interest rate", min_value=0.0, max_value=0.15, value=0.05, step=0.005)
    vol = st.slider("Volatility", min_value=0.01, max_value=1.0, value=0.2, step=0.01)
    time = st.slider("Time to expiry (years)", min_value=0.05, max_value=3.0, value=1.0, step=0.05)
    option_type = st.radio("Option type", options=["call", "put"], horizontal=True)

params = OptionParams(spot=spot, strike=strike, rate=rate, vol=vol, time=time, option_type=option_type)

bs_price = bs.price(params)
bs_greeks = bs.greeks(params)
euro_tree_price = binomial.price_european(params, steps=BINOMIAL_STEPS)
american_tree_price = binomial.price_american(params, steps=BINOMIAL_STEPS)
mc_result = mc.price(params, n_paths=MC_PATHS, seed=MC_SEED)

st.metric("Price", f"${bs_price:,.2f}")
st.caption(f"This option would cost about ${bs_price:,.2f} today.")

st.subheader("Three ways of calculating this")
col1, col2, col3 = st.columns(3)
col1.metric("Black-Scholes", f"${bs_price:,.2f}")
col2.metric("Binomial tree", f"${euro_tree_price:,.2f}")
col3.metric("Monte Carlo", f"${mc_result.price:,.2f}")
st.caption(f"Monte Carlo 95% confidence interval: ${mc_result.ci_low:,.2f} - ${mc_result.ci_high:,.2f}")

st.caption(f"American-style version (early exercise allowed): ${american_tree_price:,.2f}")

with st.expander("What affects this price?"):
    st.write(f"Delta: {bs_greeks.delta:.4f}")
    st.write(f"Gamma: {bs_greeks.gamma:.4f}")
    st.write(f"Vega: {bs_greeks.vega:.4f}")
    st.write(f"Theta: {bs_greeks.theta:.4f}")
    st.write(f"Rho: {bs_greeks.rho:.4f}")

with st.expander("How was this calculated?"):
    st.write("Binomial tree convergence")
    st.pyplot(charts.binomial_convergence_figure(params, bs_price, BINOMIAL_STEPS))
    st.write("Monte Carlo convergence")
    st.pyplot(charts.monte_carlo_convergence_figure(params, bs_price, MC_PATHS, MC_SEED))
