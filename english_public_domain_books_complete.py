import os
import re
import csv
import argparse
import tarfile
import glob
import xml.etree.ElementTree as ET
from urllib.request import urlretrieve
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import pandas as pd
import requests
from bs4 import BeautifulSoup

# Constants
RDF_TAR_URL = "https://www.gutenberg.org/cache/epub/feeds/rdf-files.tar.bz2"
RDF_TAR_FILE = "rdf-files.tar.bz2"
EXTRACT_DIR = Path("rdf-files")
CHECKLIST_DIR = Path("checklist_books")
CSV_FILE = "english_public_domain_books_complete.csv"

NS = {
    'dcterms': 'http://purl.org/dc/terms/',
    'pgterms': 'http://www.gutenberg.org/2009/pgterms/',
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
}

# --- Setup Steps ---
def download_rdf():
    if not os.path.exists(RDF_TAR_FILE):
        print("📦 Downloading RDF catalog...")
        urlretrieve(RDF_TAR_URL, RDF_TAR_FILE)
    else:
        print("✅ RDF file already downloaded.")

def extract_rdf():
    if not EXTRACT_DIR.exists():
        print("📂 Extracting RDF files...")
        with tarfile.open(RDF_TAR_FILE, "r:bz2") as tar:
            tar.extractall(EXTRACT_DIR)
    else:
        print("✅ RDF files already extracted.")

# --- Core Parsing Logic ---
def is_public_domain(tree):
    for elem in tree.iter():
        if elem.tag.endswith("rights") and elem.text and "public domain" in elem.text.lower():
            return True
    return False

def parse_rdf(rdf_path, year_cutoff, scrape_fallback=False):
    try:
        tree = ET.parse(rdf_path)
        root = tree.getroot()

        # Language filter (English)
        lang = root.find(".//dcterms:language//rdf:Description/rdf:value", NS)
        if lang is None or lang.text.strip().lower() != "en":
            return None

        title = root.find(".//dcterms:title", NS)
        author = root.find(".//pgterms:agent/pgterms:name", NS)
        date = root.find(".//dcterms:issued", NS) or root.find(".//dcterms:date", NS)
        year = None
        if date is not None and date.text:
            match = re.search(r'\d{4}', date.text)
            if match:
                year = int(match.group())

        pub_flag = is_public_domain(tree)

        # Fallback scraping
        if scrape_fallback and not pub_flag and (year is None or year > year_cutoff):
            book_id = Path(rdf_path).stem.replace("pg", "").replace(".rdf", "")
            url = f"https://www.gutenberg.org/ebooks/{book_id}"
            try:
                res = requests.get(url, timeout=10)
                soup = BeautifulSoup(res.text, 'html.parser')
                td = soup.find('td', string=re.compile(r"copyright status", re.IGNORECASE))
                if td:
                    status = td.find_next_sibling('td')
                    if status and "public domain" in status.text.lower():
                        pub_flag = True
            except Exception as e:
                print(f"⚠️ Error scraping {url}: {e}")

        if not pub_flag and (year is None or year > year_cutoff):
            return None

        book_id = Path(rdf_path).stem.replace("pg", "").replace(".rdf", "")
        return {
            "id": book_id,
            "title": title.text.strip() if title is not None else "Unknown Title",
            "author": author.text.strip() if author is not None else "Unknown Author",
            "year": year
        }
    except Exception as e:
        return None

def build_checklist(year_cutoff, scrape_fallback):
    download_rdf()
    extract_rdf()

    rdf_files = list(EXTRACT_DIR.rglob("*.rdf"))
    print(f"🔍 Parsing {len(rdf_files)} RDF files...")

    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(parse_rdf, f, year_cutoff, scrape_fallback) for f in rdf_files]
        for f in tqdm(as_completed(futures), total=len(futures), desc="Parsing"):
            result = f.result()
            if result:
                results.append(result)

    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "title", "author", "year", "completed"])
        writer.writeheader()
        for row in results:
            row["completed"] = 0
            writer.writerow(row)

    print(f"✅ Found {len(results)} books. Saved to {CSV_FILE}.")

# --- Checklist Operations ---
def show_checklist():
    if not Path(CSV_FILE).exists():
        print("❌ CSV not found. Run 'build' first.")
        return

    with open(CSV_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            book_path = CHECKLIST_DIR / f"{row['id']}.txt"
            status = "✅" if book_path.exists() else "❌"
            print(f"[{status}] {row['title']} by {row['author']} (ID: {row['id']})")

def mark_book(book_id):
    CHECKLIST_DIR.mkdir(exist_ok=True)
    path = CHECKLIST_DIR / f"{book_id}.txt"
    path.touch()
    print(f"✅ Marked book {book_id} as completed.")

def unmark_book(book_id):
    path = CHECKLIST_DIR / f"{book_id}.txt"
    if path.exists():
        path.unlink()
        print(f"❌ Unmarked book {book_id}.")
    else:
        print(f"⚠️ Book {book_id} not found.")

def search_books(keyword):
    if not Path(CSV_FILE).exists():
        print("❌ CSV not found. Run 'build' first.")
        return
    keyword = keyword.lower()
    found = False
    with open(CSV_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if keyword in row['title'].lower() or keyword in row['author'].lower():
                found = True
                book_path = CHECKLIST_DIR / f"{row['id']}.txt"
                status = "✅" if book_path.exists() else "❌"
                print(f"[{status}] {row['title']} by {row['author']} (ID: {row['id']})")
    if not found:
        print(f"🔍 No results found for '{keyword}'.")

# --- CLI ---
def main():
    parser = argparse.ArgumentParser(description="Public Domain Book Tool")
    subparsers = parser.add_subparsers(dest="command")

    build = subparsers.add_parser("build")
    build.add_argument("--year", type=int, default=1927, help="Public domain year cutoff")
    build.add_argument("--scrape", action="store_true", help="Enable fallback copyright scraping")

    subparsers.add_parser("show")
    subparsers.add_parser("checklist")
    subparsers.add_parser("search").add_argument("keyword", nargs="+")
    subparsers.add_parser("mark").add_argument("book_id")
    subparsers.add_parser("unmark").add_argument("book_id")

    args = parser.parse_args()

    if args.command == "build":
        build_checklist(year_cutoff=args.year, scrape_fallback=args.scrape)
    elif args.command == "show" or args.command == "checklist":
        show_checklist()
    elif args.command == "mark":
        mark_book(args.book_id)
    elif args.command == "unmark":
        unmark_book(args.book_id)
    elif args.command == "search":
        search_books(" ".join(args.keyword))
    else:
        parser.print_help()

if __name__ == "__main__":
    main()