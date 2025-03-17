#!/usr/bin/env python3
import sys
import urllib.parse
import xml.dom.minidom
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Tag
import random
from time import sleep
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class SitemapGenerator:
    def __init__(self, base_url, debug=False):
        self.base_url = base_url
        self.base_domain = urllib.parse.urlparse(base_url).netloc
        self.debug = debug
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
        self.session.mount("http://", HTTPAdapter(max_retries=retry_strategy))

        # Sets for tracking
        self.to_visit = set([base_url])
        self.visited = set()

        # XML document for sitemap
        try:
            dom_impl = xml.dom.minidom.getDOMImplementation()
            if dom_impl:
                self.xml_doc = dom_impl.createDocument(None, "urlset", None)
            else:
                self.xml_doc = xml.dom.minidom.Document()
                root = self.xml_doc.createElement("urlset")
                self.xml_doc.appendChild(root)
            self.xml_doc.documentElement.setAttribute(
                "xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9"
            )
        except Exception as e:
            sys.stderr.write(f"Error initializing XML document: {str(e)}\n")
            raise

    def get_frequency_priority(self, url_depth):
        """Determine change frequency and priority based on URL depth"""
        if url_depth == 0:  # Base URL
            return "daily", "1.0"
        elif url_depth == 1:  # First level
            return "weekly", "0.8"
        elif url_depth == 2:  # Second level
            return "monthly", "0.6"
        else:  # Deeper levels
            return "monthly", "0.4"

    def add_url_to_sitemap(self, url):
        """Add a URL to the XML sitemap"""
        try:
            # Calculate URL depth
            path = urllib.parse.urlparse(url).path
            depth = len([p for p in path.split("/") if p]) if path != "/" else 0

            # Get frequency and priority
            changefreq, priority = self.get_frequency_priority(depth)

            # Create a new URL element
            url_element = self.xml_doc.createElement("url")

            # Add location
            loc = self.xml_doc.createElement("loc")
            loc_text = self.xml_doc.createTextNode(url)
            loc.appendChild(loc_text)
            url_element.appendChild(loc)

            # Add last modified (current date for simplicity)
            lastmod = self.xml_doc.createElement("lastmod")
            date = datetime.now().strftime("%Y-%m-%d")
            lastmod_text = self.xml_doc.createTextNode(date)
            lastmod.appendChild(lastmod_text)
            url_element.appendChild(lastmod)

            # Add change frequency
            freq = self.xml_doc.createElement("changefreq")
            freq_text = self.xml_doc.createTextNode(changefreq)
            freq.appendChild(freq_text)
            url_element.appendChild(freq)

            # Add priority
            priority_elem = self.xml_doc.createElement("priority")
            priority_text = self.xml_doc.createTextNode(priority)
            priority_elem.appendChild(priority_text)
            url_element.appendChild(priority_elem)

            # Add to the document
            self.xml_doc.documentElement.appendChild(url_element)
        except Exception as e:
            sys.stderr.write(f"Error adding URL to sitemap: {str(e)}\n")

    def process_url(self, url):
        """Process a single URL: fetch and extract links"""
        try:
            print(f"Processing: {url}")
            
            # Add random delay between requests (1-3 seconds)
            sleep(random.uniform(1, 3))
            
            # Fetch the URL using session with retry logic
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            response = self.session.get(url, headers=headers, timeout=10)

            # Skip if not successful
            if response.status_code != 200:
                if self.debug:
                    sys.stderr.write(f"Skipping {url} - Status code: {response.status_code}\n")
                return

            # Check content type
            content_type = response.headers.get("Content-Type", "").lower()
            if not ("text/html" in content_type or "application/xhtml+xml" in content_type):
                if self.debug:
                    sys.stderr.write(f"Skipping {url} - Content type: {content_type}\n")
                return

            # Parse HTML
            try:
                soup = BeautifulSoup(response.text, "html.parser")
            except Exception as e:
                if self.debug:
                    sys.stderr.write(f"Failed to parse HTML for {url}: {str(e)}\n")
                return

            # Add this URL to the sitemap
            self.add_url_to_sitemap(url)

            # Extract links
            for link in soup.find_all("a"):
                try:
                    if not isinstance(link, Tag):
                        continue
                    
                    href = link.get("href")
                    if not href:
                        continue

                    href = str(href)

                    # Skip fragment or non-HTTP links
                    if (
                        href.startswith("#")
                        or href.startswith("javascript:")
                        or href.startswith("mailto:")
                    ):
                        continue

                    # Handle relative URLs
                    if not href.startswith(("http://", "https://")):
                        href = urllib.parse.urljoin(url, href)

                    # Parse and clean the URL
                    parsed_href = urllib.parse.urlparse(href)
                    
                    # Only allow exact domain match (no subdomains) and must start with base_url
                    if parsed_href.netloc != self.base_domain or not href.startswith(self.base_url):
                        continue
                    
                    # Skip links with file extensions we want to exclude
                    excluded_extensions = [
                        ".pdf", ".doc", ".docx", ".xls", ".xlsx",
                        ".jpg", ".jpeg", ".png", ".gif"
                    ]
                    if any(parsed_href.path.endswith(ext) for ext in excluded_extensions):
                        continue

                    # Normalize URL
                    normalized_href = urllib.parse.urlunparse(
                        (
                            parsed_href.scheme,
                            parsed_href.netloc,
                            parsed_href.path,
                            "",
                            "",
                            "",
                        )
                    )

                    # Add to queue if not visited
                    if normalized_href not in self.visited:
                        self.to_visit.add(normalized_href)

                except Exception as link_error:
                    if self.debug:
                        sys.stderr.write(f"Error processing link: {str(link_error)}\n")
                    continue

            return True
        except Exception as e:
            if self.debug:
                sys.stderr.write(f"Error processing {url}: {str(e)}\n")
            return False

    def generate_sitemap(self):
        """Generate the sitemap sequentially"""
        try:
            while self.to_visit:
                # Get next URL to process
                url = self.to_visit.pop()
                if url not in self.visited:
                    self.visited.add(url)
                    self.process_url(url)

            # Generate the XML file
            xml_string = self.xml_doc.toprettyxml(indent="  ", encoding="utf-8")
            with open("sitemap.xml", "wb") as f:
                f.write(xml_string)

            return len(self.visited)
        except KeyboardInterrupt:
            print("\nSitemap generation interrupted. Saving progress...")
            xml_string = self.xml_doc.toprettyxml(indent="  ", encoding="utf-8")
            with open("sitemap.xml", "wb") as f:
                f.write(xml_string)
            return len(self.visited)
        except Exception as e:
            sys.stderr.write(f"Error in sitemap generation: {str(e)}\n")
            return 0


def main():
    """Main function to run the sitemap generator"""
    base_url = "https://www.overleaf.com/learn"
    debug = True  # Enable debug mode for article tracking

    print(f"Starting sitemap generation for {base_url}")
    print("Press Ctrl+C to stop and save current progress")

    # Create generator and start the process
    generator = SitemapGenerator(base_url, debug)
    url_count = generator.generate_sitemap()

    print(f"\nSitemap generation complete!")
    print(f"Total URLs processed: {url_count}")
    print(f"Sitemap saved to: sitemap.xml")


if __name__ == "__main__":
    main()
