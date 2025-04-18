import logging
import os
from typing import Optional
import xml.etree.ElementTree as ET

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


def CDATA(text=None):
    """Create a CDATA element"""
    element = ET.Element(CDATA)
    element.text = text
    return element


class ElementTreeCDATA(ET.ElementTree):
    """Extended ElementTree that properly handles CDATA sections"""

    def _write(self, file, node, encoding, namespaces):
        if node.tag is CDATA:
            text = node.text.encode(encoding) if encoding else node.text
            file.write("<![CDATA[%s]]>" % text)
        else:
            super()._write(file, node, encoding, namespaces)


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

    # Add back only Iraq-related items with proper CDATA handling
    iraq_items = 0
    for item in items:
        link = item.find("link")
        if link is not None and "/iraq/" in link.text.lower():
            # Handle description CDATA
            old_desc = item.find("description")
            if old_desc is not None:
                item.remove(old_desc)
                new_desc = ET.SubElement(item, "description")
                new_desc.append(CDATA(old_desc.text))

            # Handle content:encoded CDATA
            content = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
            if content is not None:
                item.remove(content)
                new_content = ET.SubElement(
                    item, "{http://purl.org/rss/1.0/modules/content/}encoded"
                )
                new_content.append(CDATA(content.text))

            # Handle creator CDATA
            creator = item.find("{http://purl.org/dc/elements/1.1/}creator")
            if creator is not None:
                item.remove(creator)
                new_creator = ET.SubElement(
                    item, "{http://purl.org/dc/elements/1.1/}creator"
                )
                new_creator.append(CDATA(creator.text))

            # Handle category CDATA
            for category in item.findall("category"):
                item.remove(category)
                new_cat = ET.SubElement(item, "category")
                new_cat.append(CDATA(category.text))

            channel.append(item)
            iraq_items += 1

    logger.info(
        f"Filtered feed: {iraq_items} Iraq-related items out of {len(items)} total items"
    )

    # Convert to string with proper CDATA handling
    tree = ElementTreeCDATA(root)
    from io import BytesIO

    output = BytesIO()
    tree.write(output, encoding="utf-8", xml_declaration=True)
    return output.getvalue().decode("utf-8")


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
