import logging
import os
from typing import Optional

import cloudscraper
from flask import Flask, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from lxml import etree
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

    url = "https://www.iraqinews.com/rss/"

    try:
        logger.info(f"Fetching feed from {url}")
        response = scraper.get(url, timeout=10)
        response.raise_for_status()
        logger.info(f"Successfully fetched feed (status code: {response.status_code})")
        return response.text
    except Exception as e:
        logger.error(f"Failed to fetch feed: {str(e)}")
        return None


def filter_feed(feed_content: str) -> str:
    """Filter the feed to keep only Iraq-related articles"""
    # Parse the feed with lxml
    parser = etree.XMLParser(strip_cdata=False, remove_blank_text=True)
    root = etree.fromstring(feed_content.encode("utf-8"), parser)

    # Find all items
    items = root.findall(".//item")
    channel = root.find("channel")

    if channel is None:
        logger.error("Invalid feed format: no channel element found")
        return feed_content

    # Remove all items from channel
    for item in items:
        parent = item.getparent()
        if parent is not None:
            parent.remove(item)

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

    # Convert back to string preserving CDATA and formatting
    return etree.tostring(
        root, encoding="utf-8", xml_declaration=True, pretty_print=True, method="xml"
    ).decode("utf-8")


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
    port = int(os.environ.get("PORT", 8080))
    host = "localhost" if os.environ.get("FLASK_ENV") == "development" else "0.0.0.0"
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host=host, port=port, debug=debug)
