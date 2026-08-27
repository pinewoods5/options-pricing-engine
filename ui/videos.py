"""Educational video links for each pricing method.

Each entry was checked live via YouTube's oEmbed endpoint
(https://www.youtube.com/oembed?url=...) before being added here, and the
title/channel below were pulled directly from that response. Embedding at
runtime uses the standard youtube-nocookie.com /embed/<id> iframe URL --
YouTube's own sanctioned embed pattern, not scraped content.
"""

VIDEOS = {
    "black_scholes": {
        "title": "Introduction to the Black-Scholes formula",
        "channel": "Khan Academy",
        "youtube_id": "pr-u4LCFYEY",
    },
    "binomial": {
        "title": "Pricing Options Using the Binomial Tree (Risk Neutral Valuation Approach)",
        "channel": "Patrick Boyle",
        "youtube_id": "-m5jabeJJBs",
    },
    "monte_carlo": {
        "title": "How to Price Options with Monte Carlo Simulation",
        "channel": "Quant Guild",
        "youtube_id": "2-VRYBKfoyE",
    },
}


def embed_html(youtube_id: str) -> str:
    """A standard responsive YouTube iframe embed for the given video id."""
    return f"""
    <div style="position:relative; padding-bottom:56.25%; height:0; overflow:hidden; border-radius:8px;">
        <iframe
            src="https://www.youtube-nocookie.com/embed/{youtube_id}"
            title="YouTube video player"
            style="position:absolute; top:0; left:0; width:100%; height:100%; border:0;"
            loading="lazy"
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
            allowfullscreen>
        </iframe>
    </div>
    """
