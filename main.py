import logging
import os
from typing import Optional

import cloudscraper
from flask import Flask, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from lxml import etree, html
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

    url = "https://www.iraqinews.com/feed/"

    try:
        logger.info(f"Fetching feed from {url}")
        response = scraper.get(url, timeout=10)
        response.raise_for_status()
        logger.info(f"Successfully fetched feed (status code: {response.status_code})")
        return response.text
    except Exception as e:
        logger.error(f"Failed to fetch feed: {str(e)}")
        return None


def create_cdata_element(tag: str, text: str, parent) -> etree._Element:
    """Create an element with CDATA content with proper formatting"""
    elem = etree.SubElement(parent, tag)
    # Format CDATA content with proper newlines and spacing
    formatted_text = f"\n{text}\n"
    elem.text = etree.CDATA(formatted_text)
    return elem


def filter_feed(feed_content: str) -> str:
    """Filter the feed to keep only Iraq-related articles"""
    # Parse the feed with lxml
    parser = etree.XMLParser(strip_cdata=False, remove_blank_text=True, recover=True)
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
            # Create new item with proper CDATA sections
            new_item = etree.SubElement(channel, "item")

            # Copy title and link
            title = etree.SubElement(new_item, "title")
            title.text = item.find("title").text
            link_elem = etree.SubElement(new_item, "link")
            link_elem.text = link.text

            # Add creator with CDATA
            creator = item.find("{http://purl.org/dc/elements/1.1/}creator")
            if creator is not None:
                create_cdata_element(
                    "{http://purl.org/dc/elements/1.1/}creator",
                    creator.text.strip(),
                    new_item,
                )

            # Add publication date
            pub_date = item.find("pubDate")
            if pub_date is not None:
                pub_date_elem = etree.SubElement(new_item, "pubDate")
                pub_date_elem.text = pub_date.text

            # Add categories with CDATA
            for category in item.findall("category"):
                create_cdata_element("category", category.text.strip(), new_item)

            # Add guid
            guid = item.find("guid")
            if guid is not None:
                guid_elem = etree.SubElement(new_item, "guid")
                guid_elem.text = guid.text
                if guid.get("isPermaLink"):
                    guid_elem.set("isPermaLink", guid.get("isPermaLink"))

            # Add description with CDATA
            desc = item.find("description")
            if desc is not None:
                desc_text = (
                    desc.text if desc.text else html.tostring(desc, encoding="unicode")
                )
                # Format description with proper newlines
                formatted_desc = f"\n{desc_text.strip()}\n"
                create_cdata_element("description", formatted_desc, new_item)

            # Add content:encoded with CDATA
            content = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
            if content is not None:
                content_text = (
                    content.text
                    if content.text
                    else html.tostring(content, encoding="unicode")
                )
                # Format content with proper newlines
                formatted_content = f"\n{content_text.strip()}\n"
                create_cdata_element(
                    "{http://purl.org/rss/1.0/modules/content/}encoded",
                    formatted_content,
                    new_item,
                )

            iraq_items += 1

    logger.info(
        f"Filtered feed: {iraq_items} Iraq-related items out of {len(items)} total items"
    )

    # Convert back to string preserving CDATA and formatting
    return etree.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=True,
        method="xml",
        with_tail=False,
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
