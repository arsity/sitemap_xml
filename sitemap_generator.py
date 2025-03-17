#!/usr/bin/env python3
import sys
import time
import queue
import threading
import urllib.parse
import xml.dom.minidom
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from bs4 import BeautifulSoup, Tag
import curses
import random
from time import sleep
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class SitemapGenerator:
    def __init__(self, base_url, max_workers=5, debug=False):
        self.base_url = base_url
        self.base_domain = urllib.parse.urlparse(base_url).netloc
        self.max_workers = max_workers
        self.debug = debug
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,  # number of retries
            backoff_factor=1,  # wait 1, 2, 4 seconds between retries
            status_forcelist=[429, 500, 502, 503, 504],  # retry on these status codes
        )
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=retry_strategy))
        self.session.mount("http://", HTTPAdapter(max_retries=retry_strategy))

        # Queues and sets for tracking
        self.to_visit = queue.Queue()
        self.visited = set()
        self.url_frequencies = {}  # Store URLs with their frequencies

        # Add the base URL to start
        self.to_visit.put(base_url)

        # For terminal display
        self.current_url = "Initializing..."
        self.lock = threading.Lock()

        # XML document for sitemap
        try:
            dom_impl = xml.dom.minidom.getDOMImplementation()
            if dom_impl:
                self.xml_doc = dom_impl.createDocument(None, "urlset", None)
            else:
                # Fallback if implementation not available
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
            with self.lock:
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
        """Process a single URL: fetch, extract links, and update tracking"""
        try:
            # Update current URL for display
            with self.lock:
                self.current_url = url

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

            # Check content type, but be more lenient
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
                    # Skip if not a Tag object or doesn't have href
                    if not isinstance(link, Tag):
                        continue
                    
                    href = link.get("href")
                    if not href:
                        continue

                    # Log article links in debug mode
                    if self.debug and "/learn/latex/Articles" in str(href):
                        sys.stderr.write(f"Found article link: {href}\n")

                    # Convert to string to ensure proper type
                    href = str(href)

                    # Skip fragment, or non-HTTP links
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
                    
                    # Handle article links specifically
                    if parsed_href.netloc == self.base_domain:
                        # Allow URLs that either start with base_url or are article links
                        if not (href.startswith(self.base_url) or 
                              "/learn/latex/" in href or 
                              "/learn/latex/Articles" in href):
                            continue
                    else:
                        continue  # Skip external links
                    
                    # Skip links with file extensions we want to exclude
                    excluded_extensions = [
                        ".pdf", ".doc", ".docx", ".xls", ".xlsx",
                        ".jpg", ".jpeg", ".png", ".gif"
                    ]
                    if any(parsed_href.path.endswith(ext) for ext in excluded_extensions):
                        continue

                    # Normalize URL by removing fragments and query parameters
                    normalized_href = urllib.parse.urlunparse(
                        (
                            parsed_href.scheme,
                            parsed_href.netloc,
                            parsed_href.path,
                            "",  # params
                            "",  # query
                            "",  # fragment
                        )
                    )

                    # Check if we've visited this URL or queued it already
                    with self.lock:
                        if (
                            normalized_href not in self.visited
                            and normalized_href not in self.url_frequencies
                        ):
                            self.to_visit.put(normalized_href)
                            self.url_frequencies[normalized_href] = True  # Mark as queued
                except Exception as link_error:
                    if self.debug:
                        sys.stderr.write(f"Error processing link: {str(link_error)}\n")
                    continue

            return True
        except Exception as e:
            if self.debug:
                sys.stderr.write(f"Error processing {url}: {str(e)}\n")
            return False

    def display_status(self, stdscr):
        """Update the terminal display with current status"""
        try:
            while True:
                with self.lock:
                    # Get window height and width
                    height, width = stdscr.getmaxyx()

                    # Clear screen
                    stdscr.clear()

                    # Display current URL at the top (first line)
                    current_display = f"Current URL: {self.current_url}"
                    if len(current_display) > width - 1:
                        current_display = current_display[: width - 4] + "..."
                    stdscr.addstr(0, 0, current_display)

                    # Display stats at the bottom
                    visited_count = len(self.visited)
                    queue_size = self.to_visit.qsize()
                    stats = f"Visited: {visited_count} | Queue: {queue_size}"
                    stdscr.addstr(height - 1, 0, stats)

                    # Refresh the screen
                    stdscr.refresh()

                # Sleep to reduce CPU usage
                time.sleep(0.1)
        except Exception as e:
            # Clean up in case of error
            curses.endwin()
            sys.stderr.write(f"Display error: {str(e)}\n")

    def generate_sitemap(self):
        """Generate the sitemap using parallel processing"""
        try:
            # Start the display in a separate thread
            display_thread = threading.Thread(
                target=lambda: curses.wrapper(self.display_status)
            )
            display_thread.daemon = True
            display_thread.start()

            # Process URLs in parallel using a thread pool
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = []

                while True:
                    # Get current batch of URLs to process
                    batch_urls = []
                    while len(batch_urls) < self.max_workers:
                        try:
                            # Non-blocking queue get with timeout
                            url = self.to_visit.get(block=True, timeout=0.1)
                            if url not in self.visited:
                                batch_urls.append(url)
                        except queue.Empty:
                            break

                    # Submit all URLs in the batch to the thread pool
                    for url in batch_urls:
                        with self.lock:
                            self.visited.add(url)
                        futures.append(executor.submit(self.process_url, url))

                    # Wait for current batch to complete
                    for future in futures:
                        future.result()
                    futures = []

                    # Check if we're done
                    if self.to_visit.empty():
                        break

                    # Small delay to allow for display updates
                    time.sleep(0.05)

            # Generate the XML file
            xml_string = self.xml_doc.toprettyxml(indent="  ", encoding="utf-8")
            with open("sitemap.xml", "wb") as f:
                f.write(xml_string)

            return len(self.visited)
        except KeyboardInterrupt:
            # Handle Ctrl+C gracefully
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
    max_workers = 5  # Reduced number of workers to avoid rate limiting
    debug = True  # Enable debug mode for article tracking

    print(f"Starting sitemap generation for {base_url}")
    print(f"Using {max_workers} parallel workers")
    print("Press Ctrl+C to stop and save current progress")

    # Create generator and start the process
    generator = SitemapGenerator(base_url, max_workers, debug)
    url_count = generator.generate_sitemap()

    print(f"\nSitemap generation complete!")
    print(f"Total URLs processed: {url_count}")
    print(f"Sitemap saved to: sitemap.xml")


if __name__ == "__main__":
    main()
