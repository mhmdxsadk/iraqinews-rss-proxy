import logging
import os
from typing import Optional

import cloudscraper
from flask import Flask, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)


def fetch_feed() -> Optional[str]:
    """Fetch the original RSS feed from iraqinews.com"""
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "darwin", "desktop": True}
    )

    urls = [
        "https://www.iraqinews.com/rss",
        "https://www.iraqinews.com/feed",
        "https://iraqinews.com/rss",
        "https://iraqinews.com/feed",
    ]

    for url in urls:
        try:
            logger.info(f"Trying to fetch feed from {url}")
            response = scraper.get(url, timeout=10)
            response.raise_for_status()
            logger.info(f"Successfully fetched feed from {url}")
            return response.text
        except Exception as e:
            logger.warning(f"Failed to fetch from {url}: {str(e)}")
            continue

    return None


def filter_feed(feed_content: str) -> str:
    """Filter the feed to keep only Iraq-related articles"""
    import xml.etree.ElementTree as ET

    # Parse the feed
    root = ET.fromstring(feed_content)
    channel = root.find("channel")

    if channel is None:
        logger.error("Invalid feed format: no channel element found")
        return feed_content

    # Store all items
    items = channel.findall("item")

    # Remove all items from channel
    for item in items:
        channel.remove(item)

    # Add back only Iraq-related items
    iraq_items = 0
    for item in items:
        link = item.find("link")
        if link is not None and "/iraq/" in link.text.lower():
            channel.append(item)
            iraq_items += 1

    logger.info(
        f"Filtered feed: {iraq_items} Iraq-related items out of {len(items)} total items"
    )

    # Convert back to string while preserving CDATA
    feed_str = ET.tostring(root, encoding="unicode", method="xml")

    # Fix CDATA sections that might have been escaped
    feed_str = feed_str.replace("&lt;![CDATA[", "<![CDATA[").replace("]]&gt;", "]]>")

    # Add XML declaration
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{feed_str}'


# Initialize Flask app
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Initialize rate limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["30 per minute", "1 per second"],
    storage_uri="memory://",
)


@app.route("/")
@limiter.limit("30 per minute")
def filtered_feed():
    """Main route handler for the filtered feed"""
    try:
        # Fetch the original feed
        feed_content = fetch_feed()
        if feed_content is None:
            return Response("Failed to fetch the RSS feed", status=503)

        # Filter the feed
        filtered_content = filter_feed(feed_content)

        # Return the filtered feed
        response = Response(filtered_content, mimetype="application/rss+xml")
        response.headers["Cache-Control"] = "public, max-age=300"
        return response

    except Exception as e:
        logger.exception("Error processing feed")
        return Response(f"Internal server error: {str(e)}", status=500)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5555))
    host = "localhost" if os.environ.get("FLASK_ENV") == "development" else "0.0.0.0"
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host=host, port=port, debug=debug)
