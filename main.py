import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import lru_cache
from typing import Dict, List, Optional

import cloudscraper
import feedparser
from flask import Flask, Response
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s",
)
logger = logging.getLogger(__name__)


class FeedEntry:
    """Represents a single feed entry with validation and XML generation"""

    def __init__(
        self,
        title: str,
        link: str,
        description: str,
        published: Optional[str] = None,
        content: Optional[str] = None,
        media_content: Optional[List[Dict]] = None,
    ):
        self.title = title
        self.link = link
        self.description = description
        self.published = published
        self.content = content
        self.media_content = media_content or []

    def to_xml(self) -> ET.Element:
        """Convert entry to XML element"""
        item = ET.Element("item")

        # Basic elements
        elements = {
            "title": self.title,
            "link": self.link,
            "description": self.description,
        }

        for key, value in elements.items():
            element = ET.SubElement(item, key)
            element.text = value

        if self.published:
            pub_date = ET.SubElement(item, "pubDate")
            pub_date.text = self.published

        # Add content if available
        if self.content:
            content = ET.SubElement(
                item,
                "content:encoded",
                xmlns="http://purl.org/rss/1.0/modules/content/",
            )
            content.text = self.content

        # Add media content if available
        for media in self.media_content:
            media_element = ET.SubElement(
                item, "media:content", xmlns="http://search.yahoo.com/mrss/"
            )
            for key, value in media.items():
                media_element.set(key, value)

        return item


class FeedManager:
    """Manages feed fetching, parsing, and filtering"""

    def __init__(self, base_url: str = "https://iraqinews.com"):
        self.base_url = base_url
        self.scraper = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "darwin", "desktop": True},
            delay=10,
        )
        self.feed_urls = [
            f"{base_url}/feed/",
            f"https://www.{base_url.split('://')[1]}/feed/",
            f"{base_url}/rss/",
            f"https://www.{base_url.split('://')[1]}/rss/",
        ]

    @staticmethod
    def is_valid_url(url: str) -> bool:
        """Validate URL format"""
        url_pattern = re.compile(
            r"^https?://"
            r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"
            r"localhost|"
            r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
            r"(?::\d+)?"
            r"(?:/?|[/?]\S+)$",
            re.IGNORECASE,
        )
        return bool(url_pattern.match(url))

    @lru_cache(maxsize=1)
    def fetch_feed(self, feed_url: str, timestamp: int) -> feedparser.FeedParserDict:
        """Fetch and parse the RSS feed with caching"""
        if not self.is_valid_url(feed_url):
            raise ValueError("Invalid URL format")

        try:
            response = self.scraper.get(feed_url, timeout=10)
            response.raise_for_status()
            return feedparser.parse(response.content)
        except Exception:
            logger.exception("Error fetching feed")
            raise

    def get_filtered_entries(self) -> List[FeedEntry]:
        """Get filtered feed entries from all possible URLs"""
        timestamp = int(datetime.now().timestamp() / 300) * 300
        last_error = None

        for url in self.feed_urls:
            try:
                logger.info(f"Trying feed URL: {url}")
                feed = self.fetch_feed(url, timestamp)
                if feed.entries:
                    return [
                        FeedEntry(
                            title=entry.title,
                            link=entry.link,
                            description=entry.description,
                            published=getattr(entry, "published", None),
                            content=getattr(
                                entry, "content", [{"value": entry.description}]
                            )[0]["value"]
                            if hasattr(entry, "content")
                            else entry.description,
                            media_content=[
                                {"url": m["url"], "type": m.get("type", "image/jpeg")}
                                for m in entry.get("media_content", [])
                            ],
                        )
                        for entry in feed.entries
                        if "iraq/" in entry.link.lower()
                    ]
            except Exception as e:
                logger.warning(f"Failed to fetch from {url}: {str(e)}")
                last_error = e

        if last_error:
            logger.error(f"All feed URLs failed\nLast error: {str(last_error)}")
            raise last_error

        return []


class FeedResponse:
    """Handles XML response generation and security headers"""

    def __init__(self):
        self.headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "X-XSS-Protection": "1; mode=block",
            "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
            "Server": "",
            "Cache-Control": "public, max-age=300",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
        }

    def create_xml_response(self, entries: List[FeedEntry]) -> Response:
        """Create XML response from feed entries"""
        # Create RSS root with proper namespaces
        rss = ET.Element("rss", version="2.0")
        rss.set("xmlns:content", "http://purl.org/rss/1.0/modules/content/")
        rss.set("xmlns:media", "http://search.yahoo.com/mrss/")
        rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")

        channel = ET.SubElement(rss, "channel")

        # Add channel information
        channel_info = {
            "title": "Iraqi News - Iraq Filtered Feed",
            "link": "https://iraqinews.com/iraq",
            "description": "Filtered RSS feed from iraqinews.com containing only Iraq-related news",
            "language": "en-us",
            "lastBuildDate": datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"),
        }

        for key, value in channel_info.items():
            element = ET.SubElement(channel, key)
            element.text = value

        # Add atom:link for self-reference
        atom_link = ET.SubElement(channel, "atom:link")
        atom_link.set("href", "https://iraqinews-rss-proxy.fly.dev/")
        atom_link.set("rel", "self")
        atom_link.set("type", "application/rss+xml")

        # Add entries
        for entry in entries:
            channel.append(entry.to_xml())

        # Generate XML with proper declaration and formatting
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        xml_str = xml_declaration + ET.tostring(rss, encoding="unicode", method="xml")

        # Ensure proper RSS content type
        response = Response(xml_str, mimetype="application/rss+xml")

        # Add security headers
        for key, value in self.headers.items():
            response.headers[key] = value

        return response


# Initialize Flask app
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
Compress(app)

# Initialize components
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["30 per minute", "1 per second"],
    storage_uri="memory://",
)
feed_manager = FeedManager()
feed_response = FeedResponse()


@app.route("/")
@limiter.limit("30 per minute")
def filtered_feed():
    """Main route handler for the filtered feed"""
    try:
        entries = feed_manager.get_filtered_entries()
        if not entries:
            return Response("No entries found in the feed", status=503)

        logger.info(f"Filtered entries count: {len(entries)}")
        return feed_response.create_xml_response(entries)

    except Exception:
        logger.exception("Unexpected error in filtered_feed")
        return Response("Internal server error", status=500)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5555))
    host = "localhost" if os.environ.get("FLASK_ENV") == "development" else "0.0.0.0"
    debug = os.environ.get("FLASK_ENV") == "development"
    app.run(host=host, port=port, debug=debug)
