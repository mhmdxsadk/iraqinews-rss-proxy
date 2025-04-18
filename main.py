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
        self.content = content or description  # Fallback to description if no content
        self.media_content = media_content or []

    def to_xml(self) -> ET.Element:
        """Convert entry to XML element"""
        item = ET.Element("item")

        # Title and link
        title = ET.SubElement(item, "title")
        title.text = self.title
        link = ET.SubElement(item, "link")
        link.text = self.link

        # Description with CDATA - keep HTML for preview but clean it up
        description = ET.SubElement(item, "description")
        # Clean up the description while preserving essential HTML
        desc_text = self.description
        if desc_text.strip().startswith("<"):
            # Extract text from first paragraph or div if it exists
            match = re.search(
                r"<(?:p|div)[^>]*>(.*?)</(?:p|div)>", desc_text, re.DOTALL
            )
            if match:
                desc_text = match.group(1)
            # Remove any remaining HTML tags but keep the text
            desc_text = re.sub(r"<[^>]+>", " ", desc_text)
        # Clean up whitespace and truncate if needed
        desc_text = " ".join(desc_text.split())
        if len(desc_text) > 500:
            desc_text = desc_text[:497] + "..."
        # Wrap in a paragraph for better preview formatting
        desc_text = f"<p>{desc_text}</p>"
        description.text = f"<![CDATA[{desc_text}]]>"

        # Full content in content:encoded
        if self.content:
            content_ns = "{http://purl.org/rss/1.0/modules/content/}"
            content_elem = ET.SubElement(item, f"{content_ns}encoded")
            content_elem.text = f"<![CDATA[{self.content}]]>"

        if self.published:
            pubDate = ET.SubElement(item, "pubDate")
            pubDate.text = self.published

        # Add media content with proper namespace
        for media in self.media_content:
            media_ns = "{http://search.yahoo.com/mrss/}"
            media_content = ET.SubElement(item, f"{media_ns}content")
            for key, value in media.items():
                media_content.set(key, str(value))
            # Add media:thumbnail for better preview support
            if media.get("type", "").startswith("image/"):
                thumbnail = ET.SubElement(item, f"{media_ns}thumbnail")
                thumbnail.set("url", media["url"])

        # Add guid element
        guid = ET.SubElement(item, "guid")
        guid.set("isPermaLink", "true")
        guid.text = self.link

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
                            description=getattr(entry, "summary", entry.description),
                            published=getattr(entry, "published", None),
                            content=(
                                getattr(entry, "content", [{"value": ""}])[0].get(
                                    "value"
                                )
                                or getattr(entry, "summary", "")
                                or entry.description
                            ),
                            media_content=[
                                {
                                    "url": m.get("url", ""),
                                    "type": m.get("type", "image/jpeg"),
                                    "width": m.get("width", "800"),
                                    "height": m.get("height", "600"),
                                }
                                for m in entry.get("media_content", [])
                                if m.get("url")
                            ]
                            or self._extract_images_from_content(entry),
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

    def _extract_images_from_content(self, entry) -> List[Dict]:
        """Extract images from entry content if no media_content is present"""
        content = (
            getattr(entry, "content", [{"value": ""}])[0].get("value")
            or getattr(entry, "summary", "")
            or entry.description
        )

        # Simple regex to extract image URLs from content
        img_pattern = re.compile(r'<img[^>]+src="([^"]+)"')
        matches = img_pattern.findall(content)

        return [
            {
                "url": url,
                "type": "image/jpeg",
                "width": "800",
                "height": "600",
            }
            for url in matches
            if url.startswith("http")
        ]


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
        rss.set("xmlns:wfw", "http://wellformedweb.org/CommentAPI/")
        rss.set("xmlns:dc", "http://purl.org/dc/elements/1.1/")
        rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
        rss.set("xmlns:sy", "http://purl.org/rss/1.0/modules/syndication/")
        rss.set("xmlns:slash", "http://purl.org/rss/1.0/modules/slash/")
        rss.set("xmlns:media", "http://search.yahoo.com/mrss/")

        channel = ET.SubElement(rss, "channel")

        # Add channel information
        channel_info = {
            "title": "Iraqi News - Iraq Filtered Feed",
            "link": "https://iraqinews.com/iraq",
            "description": "Filtered RSS feed from iraqinews.com containing only Iraq-related news",
            "language": "en-US",
            "lastBuildDate": datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0000"),
            "generator": "https://wordpress.org/?v=6.6.2",
        }

        for key, value in channel_info.items():
            element = ET.SubElement(channel, key)
            element.text = value

        # Add atom:link for self-reference
        atom_link = ET.SubElement(channel, "atom:link")
        atom_link.set("href", "https://iraqinews-rss-proxy.fly.dev/")
        atom_link.set("rel", "self")
        atom_link.set("type", "application/rss+xml")

        # Add syndication info
        sy_update_period = ET.SubElement(channel, "sy:updatePeriod")
        sy_update_period.text = "hourly"
        sy_update_freq = ET.SubElement(channel, "sy:updateFrequency")
        sy_update_freq.text = "1"

        # Add entries
        for entry in entries:
            channel.append(entry.to_xml())

        # Generate XML with proper declaration
        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n'
        # Convert to string and preserve CDATA sections
        entry_str = ET.tostring(rss, encoding="unicode", method="xml")
        # Fix CDATA sections that were escaped
        entry_str = entry_str.replace("&lt;![CDATA[", "<![CDATA[").replace(
            "]]&gt;", "]]>"
        )
        xml_str += entry_str

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
