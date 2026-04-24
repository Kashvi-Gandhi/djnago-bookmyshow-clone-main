import logging

import requests

logger = logging.getLogger(__name__)


def search_trailer_video_id(movie_name: str, api_key: str, timeout_seconds: float = 2.5):
    """
    Return the first embeddable YouTube `videoId` for "<movie> trailer", or None.

    This is intentionally best-effort for local/dev environments. Production
    should prefer curated `trailer_url` values and/or webhooks/caching layers.
    """
    if not api_key or not movie_name:
        return None

    query = f"{movie_name} trailer"
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": 1,
                "videoEmbeddable": "true",
                "safeSearch": "moderate",
                "key": api_key,
            },
            timeout=timeout_seconds,
        )
        resp.raise_for_status()
        data = resp.json() or {}
        items = data.get("items") or []
        if not items:
            return None
        return (items[0].get("id") or {}).get("videoId")
    except Exception as e:
        logger.info("YouTube API lookup failed for %r: %s", query, str(e))
        return None

