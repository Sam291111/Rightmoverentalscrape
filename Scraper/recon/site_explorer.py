"""
Property Site Explorer — Diagnostic Tool
==========================================
Scroll through a property site while this script maps its structure.
At the end it produces a structured report telling you:
  - Which CSS selectors reliably find listing cards
  - Where prices, addresses, URLs, beds live inside cards
  - What lat/lng / postcode patterns exist in the page source
  - Raw HTML samples of real cards (for manual inspection)
  - Network response samples (JSON APIs the site calls internally)
  - Pagination structure
  - What changes page-to-page

Run this BEFORE writing your real scraper.

USAGE:
  pip install undetected-chromedriver selenium
  python site_explorer.py

Then browse/scroll through a results page. Press Ctrl+C to generate report.
"""

import time
import json
import re
import os
import hashlib
import threading
from datetime import datetime
from collections import defaultdict, Counter
from urllib.parse import parse_qs, urlparse

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By


# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────

CONFIG = {
    # Label for the report — just for your notes
    'site_label': 'rightmove',   # or 'zoopla', 'onthemarket', etc.

    # How often (seconds) to scan the page while you scroll
    'scan_interval': 3.0,

    # Max raw HTML card samples to save (keeps report readable)
    'max_card_samples': 5,

    # How much raw HTML from each card to keep
    'max_card_html_chars': 8000,

    # How many visible cards to inspect when ranking selectors
    'max_cards_to_probe': 8,

    # Where to save the report
    'output_dir': '.',
}

# ─────────────────────────────────────────────────────────────────
# CANDIDATE SELECTORS TO PROBE
# These are tried against the live page — survivors go in the report
# ─────────────────────────────────────────────────────────────────

CANDIDATE_CARD_SELECTORS = [
    # Rightmove
    "div[data-testid^='propertyCard-']",
    "div[data-testid^='propertyCard-vrt-']",
    "div[class*='PropertyCard_propertyCardContainer__']",
    "div[class*='PropertyCard_propertyCardContainerWrapper__']",
    "div[class*='propertyCard']",
    "div[id*='property-']",
    "div[data-test='propertyCard']",
    "l-searchResult",
    "div[class*='search-result']",
    # Zoopla
    "div[data-testid='search-result']",
    "div[data-testid='regular-listings'] > div",
    "article[data-listing-id]",
    "div[class*='ListingCard']",
    # OnTheMarket / generic
    "article",
    "li[class*='result']",
    "div[class*='listing-result']",
    "div[class*='property-result']",
    "div[class*='result-item']",
    "div[class*='ResultCard']",
    "div[class*='PropertyCard']",
    "section[class*='listing']",
]

CANDIDATE_PRICE_SELECTORS = [
    "span[data-testid='listing-price']",
    "div[class*='propertyCard-priceValue']",
    "span[class*='price']",
    "div[class*='price']",
    "p[class*='price']",
    "strong[class*='price']",
    "span[class*='Price']",
    "div[class*='Price']",
    "[class*='PriceText']",
    "[class*='price-text']",
    "[data-testid*='price']",
]

CANDIDATE_ADDRESS_SELECTORS = [
    "address",
    "h2[class*='title']",
    "h2[class*='address']",
    "span[class*='address']",
    "div[class*='address']",
    "p[class*='address']",
    "a[class*='address']",
    "[data-testid*='address']",
    "[data-testid*='title']",
    "[class*='PropertyAddress']",
    "[class*='propertyCard-address']",
]

CANDIDATE_LINK_SELECTORS = [
    "a[href*='/properties/']",        # Rightmove
    "a[href*='/for-sale/details/']",  # Zoopla
    "a[href*='/to-rent/details/']",   # Zoopla
    "a[href*='/property-for-sale/']",
    "a[href*='/property/']",
    "a[data-testid='listing-details-link']",
    "a[class*='propertyCard-link']",
    "a[class*='listing-link']",
]

CANDIDATE_BEDS_SELECTORS = [
    "[class*='bedroom']",
    "[class*='beds']",
    "[class*='Beds']",
    "span[class*='bed']",
    "[data-testid*='bed']",
    "[class*='propertyCard-details'] span",
]

CANDIDATE_PAGINATION_SELECTORS = [
    "a[data-testid='pagination-next']",
    "button[data-testid='pagination-next']",
    "a[title='Next page']",
    "a[aria-label='Next']",
    "a[class*='pagination-next']",
    ".pagination a",
    "nav[class*='pagination'] a",
    "[class*='Pagination'] a",
    "[class*='pager'] a",
]

# Regex patterns to search for in raw page source
SOURCE_PATTERNS = {
    'price_gbp':         r'£[\d,]+',
    'price_json':        r'"price"\s*:\s*(\d+)',
    'lat_lng_1':         r'"latitude"\s*:\s*([-\d.]+).*?"longitude"\s*:\s*([-\d.]+)',
    'lat_lng_2':         r'"lat"\s*:\s*([-\d.]+).*?"lng"\s*:\s*([-\d.]+)',
    'lat_lng_3':         r'lat=([-\d.]+)&lon=([-\d.]+)',
    'lat_lng_4':         r'"location"\s*:\s*\{[^}]*"lat"\s*:\s*([-\d.]+)',
    'postcode':          r'(?i)\b(?:GIR\s?0AA|(?:[A-PR-UWYZ][A-HK-Y]?\d[A-HJKPSTUW]?|[A-PR-UWYZ][A-HK-Y]?\d{2}|[A-PR-UWYZ][A-HK-Y]?\d[ABEHMNPRVWXY])\s?\d[ABD-HJLNP-UW-Z]{2})\b',
    'bedrooms':          r'\b([1-9]\d?)\s*bed(?:room)?s?\b',
    'property_type':     r'"propertyType"\s*:\s*"([^"]+)"',
    'listing_id':        r'"id"\s*:\s*(\d{6,})',
    'data_listing_id':   r'data-listing-id=["\'](\d+)["\']',
    'json_api_price':    r'"price":\{"amount":(\d+)',
    'display_address':   r'"displayAddress"\s*:\s*"([^"]+)"',
    'street_address':    r'"streetAddress"\s*:\s*"([^"]+)"',
}

BEDROOM_TEXT_RE = re.compile(r'\b([1-9]\d?)\s*bed(?:room)?s?\b', re.IGNORECASE)
UK_POSTCODE_RE = re.compile(SOURCE_PATTERNS['postcode'])


# ─────────────────────────────────────────────────────────────────
# EXPLORER
# ─────────────────────────────────────────────────────────────────

class SiteExplorer:

    def __init__(self):
        self.driver = None
        self.running = False
        self.scans = []           # Each scan is a snapshot dict
        self.seen_card_hashes = set()
        self.card_html_samples = []
        self.network_logs = []
        self.seen_network_urls = set()
        self.url_history = []
        self.lock = threading.Lock()
        self.start_time = None

    # ── Browser ────────────────────────────────────────────────────

    def setup_browser(self):
        print("🔧 Starting browser with network logging enabled...")

        options = uc.ChromeOptions()
        options.add_argument('--start-maximized')
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        self.driver = uc.Chrome(options=options, version_main=None)
        try:
            self.driver.execute_cdp_cmd('Network.enable', {})
        except Exception:
            pass
        self.start_time = datetime.now()
        print("✓ Browser ready\n")

    # ── Network log capture ────────────────────────────────────────

    def capture_network_logs(self):
        """Pull XHR/fetch calls from browser performance log."""
        try:
            logs = self.driver.get_log('performance')
            for entry in logs:
                try:
                    msg = json.loads(entry['message'])['message']
                    method = msg.get('method', '')

                    # Response received
                    if method == 'Network.responseReceived':
                        resp = msg['params']['response']
                        url = resp.get('url', '')
                        mime = resp.get('mimeType', '')
                        status = resp.get('status', 0)
                        classification = _classify_endpoint(url, CONFIG['site_label'])
                        if classification:
                            entry_data = {
                                'url': url,
                                'status': status,
                                'mime': mime,
                                'classification': classification,
                                'request_id': msg['params'].get('requestId'),
                                'time': datetime.now().isoformat(),
                            }
                            with self.lock:
                                if url not in self.seen_network_urls:
                                    self.seen_network_urls.add(url)
                                    self.network_logs.append(entry_data)
                                    print(f"  🌐 API call [{classification}]: {url[:90]}")
                except Exception:
                    continue
        except Exception:
            pass  # Performance log not available in this driver config — not critical

    # ── Single selector probe ──────────────────────────────────────

    def find_visible_elements(self, selector, context=None):
        try:
            root = context if context else self.driver
            return [e for e in root.find_elements(By.CSS_SELECTOR, selector) if e.is_displayed()]
        except Exception:
            return []

    def probe_selector(self, selector, context=None):
        """Return count and sample text of a selector match."""
        try:
            visible = self.find_visible_elements(selector, context)
            sample_text = visible[0].text.strip()[:100] if visible else None
            return len(visible), sample_text
        except Exception:
            return 0, None

    # ── Find best card selector ────────────────────────────────────

    def find_card_selector(self):
        """
        Try all candidate card selectors. Return ranked list:
        (selector, count, sample_text)
        """
        results = []
        for sel in CANDIDATE_CARD_SELECTORS:
            visible = self.find_visible_elements(sel)
            count = len(visible)
            sample = visible[0].text.strip()[:100] if visible else None
            if count >= 3:   # At least 3 matches = plausible card container
                sample_cards = visible[:CONFIG['max_cards_to_probe']]
                unique_links = set()
                unique_property_ids = set()
                price_hits = 0
                address_hits = 0
                bed_hits = 0

                for card in sample_cards:
                    findings = self.probe_card_internals(card)
                    if findings.get('best_link'):
                        unique_links.add(_canonical_listing_href(findings['best_link']))
                    if findings.get('property_id'):
                        unique_property_ids.add(findings['property_id'])
                    if findings.get('best_price'):
                        price_hits += 1
                    if findings.get('best_address'):
                        address_hits += 1
                    if findings.get('best_beds'):
                        bed_hits += 1

                score = (
                    len(unique_property_ids) * 10
                    + len(unique_links) * 8
                    + price_hits * 3
                    + address_hits * 3
                    + bed_hits * 2
                )
                if count > 150:
                    score -= min(15, (count - 150) // 10 + 1)
                if not unique_links:
                    score -= 10
                if "data-testid^='propertyCard-'" in sel or 'propertyCardContainer__' in sel:
                    score += 4

                results.append({
                    'selector': sel,
                    'count': count,
                    'sample': sample,
                    'score': score,
                    'sample_unique_listing_links': len(unique_links),
                    'sample_unique_property_ids': len(unique_property_ids),
                    'sample_price_hits': price_hits,
                    'sample_address_hits': address_hits,
                    'sample_bed_hits': bed_hits,
                })
        # Prefer selectors that resolve to one real card per listing, not nested wrappers.
        results.sort(
            key=lambda x: (
                -x['score'],
                -x['sample_unique_property_ids'],
                -x['sample_unique_listing_links'],
                x['count'],
            )
        )
        return results

    # ── Probe inside a card element ────────────────────────────────

    def probe_card_internals(self, card_el):
        """
        Given a card element, probe for price / address / link / beds.
        Returns a dict of findings.
        """
        findings = {
            'price': {},
            'address': {},
            'link': {},
            'beds': {},
            'best_price': None,
            'best_address': None,
            'best_link': None,
            'best_beds': None,
            'property_id': None,
            'all_text': card_el.text.strip()[:300],
            'data_attrs': {},
            'class': card_el.get_attribute('class') or '',
            'tag': card_el.tag_name,
        }

        # data-* attributes on the card itself (often gold mines)
        try:
            outer = card_el.get_attribute('outerHTML')
            start_tag = outer.split('>', 1)[0]
            findings['data_attrs'] = dict(
                re.findall(r'(data-[\w-]+)=["\']([^"\']*)["\']', start_tag)
            )
        except Exception:
            outer = ''

        for sel in CANDIDATE_LINK_SELECTORS:
            try:
                links = card_el.find_elements(By.CSS_SELECTOR, sel)
                for l in links:
                    href = l.get_attribute('href')
                    if href and _is_listing_href(href):
                        href = _canonical_listing_href(href)
                        findings['link'][sel] = href
                        findings['best_link'] = findings['best_link'] or href
                        findings['property_id'] = findings['property_id'] or _extract_property_id(href)
                        break
            except Exception:
                pass

        for sel in CANDIDATE_PRICE_SELECTORS:
            texts = _extract_visible_texts(self.find_visible_elements(sel, card_el))
            sample = next((t for t in texts if _looks_like_price(t)), None)
            if sample:
                findings['price'][sel] = sample
                findings['best_price'] = findings['best_price'] or sample

        for sel in CANDIDATE_ADDRESS_SELECTORS:
            texts = _extract_visible_texts(self.find_visible_elements(sel, card_el))
            sample = next((t for t in texts if _looks_like_address(t)), None)
            if sample:
                findings['address'][sel] = sample
                findings['best_address'] = findings['best_address'] or sample

        for sel in CANDIDATE_BEDS_SELECTORS:
            texts = _extract_visible_texts(self.find_visible_elements(sel, card_el))
            sample = next((t for t in texts if _extract_bedrooms(t)), None)
            if sample:
                bed_count = _extract_bedrooms(sample)
                findings['beds'][sel] = str(bed_count)
                findings['best_beds'] = findings['best_beds'] or str(bed_count)

        if not findings['property_id']:
            findings['property_id'] = (
                findings['data_attrs'].get('data-property-id')
                or _extract_property_id(outer)
                or _extract_property_id(findings.get('all_text'))
            )

        return findings

    # ── Scan page source for patterns ─────────────────────────────

    def scan_page_source(self):
        """Regex the raw page source for data patterns."""
        try:
            source = self.driver.page_source
            found = {}
            for name, pattern in SOURCE_PATTERNS.items():
                matches = re.findall(pattern, source)
                if matches:
                    normalised = [_normalise_source_match(name, m) for m in matches]
                    filtered = [m for m in normalised if m]
                    if not filtered:
                        continue
                    found[name] = {
                        'count': len(filtered),
                        'samples': list(dict.fromkeys(filtered))[:5],
                    }
            return found, len(source)
        except Exception:
            return {}, 0

    # ── Scan pagination ────────────────────────────────────────────

    def scan_pagination(self):
        """Check what pagination elements exist."""
        results = []
        extra_selectors = CANDIDATE_PAGINATION_SELECTORS + [
            "a[rel='next']",
            "a[href*='index=']",
        ]
        for sel in extra_selectors:
            count, sample = self.probe_selector(sel)
            if count:
                try:
                    el = self.driver.find_element(By.CSS_SELECTOR, sel)
                    href = el.get_attribute('href') or ''
                    text = el.text.strip()
                    results.append({
                        'selector': sel,
                        'count': count,
                        'text': text,
                        'href_sample': href[:100],
                    })
                except Exception:
                    pass
        return results

    # ── Full page scan ─────────────────────────────────────────────

    def scan(self):
        current_url = self.driver.current_url

        # Track URL changes (pagination clicks etc.)
        with self.lock:
            if not self.url_history or self.url_history[-1] != current_url:
                self.url_history.append(current_url)
                print(f"\n📍 URL: {current_url}")

        # 1. Find card selectors
        card_selectors = self.find_card_selector()

        # 2. Probe internals of first few cards from the best selector
        card_internals = []
        if card_selectors:
            best_sel = card_selectors[0]['selector']
            try:
                cards = self.driver.find_elements(By.CSS_SELECTOR, best_sel)
                visible_cards = [c for c in cards if c.is_displayed()][:CONFIG['max_cards_to_probe']]

                for card in visible_cards:
                    try:
                        internals = self.probe_card_internals(card)
                        dedupe_key = (
                            internals.get('property_id')
                            or internals.get('best_link')
                            or hashlib.md5(card.text.encode()).hexdigest()
                        )
                        h = str(dedupe_key)
                        if h not in self.seen_card_hashes:
                            self.seen_card_hashes.add(h)
                            card_internals.append(internals)

                            # Save raw HTML sample (limited count)
                            with self.lock:
                                if len(self.card_html_samples) < CONFIG['max_card_samples']:
                                    outer = card.get_attribute('outerHTML') or ''
                                    self.card_html_samples.append({
                                        'selector_used': best_sel,
                                        'property_id': internals.get('property_id'),
                                        'best_link': internals.get('best_link'),
                                        'preview_text': internals.get('all_text'),
                                        'html': outer[:CONFIG['max_card_html_chars']],
                                        'captured_from': current_url,
                                    })
                    except Exception:
                        continue

            except Exception:
                pass

        # 3. Page source patterns
        source_patterns, source_len = self.scan_page_source()

        # 4. Pagination
        pagination = self.scan_pagination()

        # 5. Network logs
        self.capture_network_logs()

        # Store snapshot
        snapshot = {
            'time': datetime.now().isoformat(),
            'url': current_url,
            'card_selectors': card_selectors,
            'card_internals_sample': card_internals,
            'source_patterns': source_patterns,
            'source_length_chars': source_len,
            'pagination': pagination,
        }

        with self.lock:
            self.scans.append(snapshot)

        # Live summary
        if card_selectors:
            best = card_selectors[0]
            print(
                f"  🃏 Best card selector: {best['selector']} "
                f"(score={best['score']}, count={best['count']})"
            )
        if card_internals:
            for ci in card_internals[:2]:
                price_hit = list(ci['price'].items())[:1]
                addr_hit  = list(ci['address'].items())[:1]
                link_hit  = list(ci['link'].items())[:1]
                print(f"  💷 Price: {price_hit[0] if price_hit else 'not found'}")
                print(f"  📍 Addr:  {addr_hit[0]  if addr_hit  else 'not found'}")
                print(f"  🔗 Link:  {link_hit[0]  if link_hit  else 'not found'}")
            print(f"  📦 {len(self.seen_card_hashes)} unique cards seen so far")

    # ── Background loop ────────────────────────────────────────────

    def scan_loop(self):
        while self.running:
            try:
                self.scan()
            except Exception as e:
                print(f"  ⚠ Scan error: {e}")
            time.sleep(CONFIG['scan_interval'])

    # ── Report generation ──────────────────────────────────────────

    def generate_report(self):
        """Synthesise all scans into a single structured report."""

        report = {
            'meta': {
                'site_label': CONFIG['site_label'],
                'generated_at': datetime.now().isoformat(),
                'urls_visited': self.url_history,
                'total_scans': len(self.scans),
                'unique_cards_seen': len(self.seen_card_hashes),
                'session_minutes': round(
                    (datetime.now() - self.start_time).total_seconds() / 60, 1
                ) if self.start_time else 0,
            },
        }

        # ── SECTION 1: Best card selectors (ranked by frequency across scans) ──
        selector_counts = Counter()
        selector_metrics = {}
        for scan in self.scans:
            for s in scan['card_selectors']:
                selector_counts[s['selector']] += 1
                current = selector_metrics.setdefault(s['selector'], {
                    'max_cards_found': 0,
                    'max_score': 0,
                    'max_unique_links': 0,
                    'max_unique_property_ids': 0,
                    'max_price_hits': 0,
                    'max_address_hits': 0,
                    'max_bed_hits': 0,
                    'sample': s.get('sample'),
                })
                current['max_cards_found'] = max(current['max_cards_found'], s['count'])
                current['max_score'] = max(current['max_score'], s.get('score', 0))
                current['max_unique_links'] = max(current['max_unique_links'], s.get('sample_unique_listing_links', 0))
                current['max_unique_property_ids'] = max(current['max_unique_property_ids'], s.get('sample_unique_property_ids', 0))
                current['max_price_hits'] = max(current['max_price_hits'], s.get('sample_price_hits', 0))
                current['max_address_hits'] = max(current['max_address_hits'], s.get('sample_address_hits', 0))
                current['max_bed_hits'] = max(current['max_bed_hits'], s.get('sample_bed_hits', 0))

        ranked_selectors = sorted(
            selector_counts,
            key=lambda sel: (
                -selector_metrics[sel]['max_score'],
                -selector_metrics[sel]['max_unique_property_ids'],
                -selector_metrics[sel]['max_unique_links'],
                selector_metrics[sel]['max_cards_found'],
            )
        )
        report['card_selectors'] = [
            {
                'selector': sel,
                'appeared_in_n_scans': selector_counts[sel],
                'max_cards_found': selector_metrics[sel]['max_cards_found'],
                'max_score': selector_metrics[sel]['max_score'],
                'max_unique_links_in_sample': selector_metrics[sel]['max_unique_links'],
                'max_unique_property_ids_in_sample': selector_metrics[sel]['max_unique_property_ids'],
                'max_price_hits_in_sample': selector_metrics[sel]['max_price_hits'],
                'max_address_hits_in_sample': selector_metrics[sel]['max_address_hits'],
                'max_bed_hits_in_sample': selector_metrics[sel]['max_bed_hits'],
                'recommended': sel == ranked_selectors[0],
            }
            for sel in ranked_selectors
        ]

        # ── SECTION 2: Field selectors (price / address / link / beds) ──
        field_hits = {
            'price': Counter(),
            'address': Counter(),
            'link': Counter(),
            'beds': Counter(),
        }
        field_samples = defaultdict(lambda: defaultdict(list))

        for scan in self.scans:
            for ci in scan.get('card_internals_sample', []):
                for field in field_hits:
                    for sel, sample in ci.get(field, {}).items():
                        field_hits[field][sel] += 1
                        if sample:
                            field_samples[field][sel].append(sample)

        report['field_selectors'] = {}
        for field, counts in field_hits.items():
            report['field_selectors'][field] = [
                {
                    'selector': sel,
                    'hit_count': count,
                    'samples': list(dict.fromkeys(field_samples[field][sel]))[:3],
                }
                for sel, count in counts.most_common(10)
            ]

        # ── SECTION 3: Page source pattern summary ──
        all_source_patterns = defaultdict(lambda: {
            'total_matches': 0,
            'max_matches_in_single_scan': 0,
            'scan_hits': 0,
            'all_samples': [],
        })
        for scan in self.scans:
            for name, data in scan.get('source_patterns', {}).items():
                all_source_patterns[name]['total_matches'] += data['count']
                all_source_patterns[name]['max_matches_in_single_scan'] = max(
                    all_source_patterns[name]['max_matches_in_single_scan'],
                    data['count'],
                )
                all_source_patterns[name]['scan_hits'] += 1
                all_source_patterns[name]['all_samples'].extend(data['samples'])

        report['source_patterns'] = {}
        for name, data in all_source_patterns.items():
            unique_samples = list(dict.fromkeys(data['all_samples']))[:8]
            report['source_patterns'][name] = {
                'total_matches_across_scans': data['total_matches'],
                'unique_matches': len(dict.fromkeys(data['all_samples'])),
                'max_matches_in_single_scan': data['max_matches_in_single_scan'],
                'seen_in_n_scans': data['scan_hits'],
                'regex': SOURCE_PATTERNS[name],
                'samples': unique_samples,
                'verdict': _pattern_verdict(name, unique_samples, len(dict.fromkeys(data['all_samples']))),
            }

        # ── SECTION 4: Pagination ──
        pagination_hits = Counter()
        pagination_samples = {}
        for scan in self.scans:
            for p in scan.get('pagination', []):
                pagination_hits[p['selector']] += 1
                pagination_samples[p['selector']] = p

        report['pagination'] = [
            {**pagination_samples[sel], 'seen_in_n_scans': pagination_hits[sel]}
            for sel in sorted(pagination_hits, key=lambda s: -pagination_hits[s])
        ]

        # ── SECTION 5: Discovered API endpoints ──
        report['api_endpoints'] = sorted(
            self.network_logs,
            key=lambda ep: (
                0 if ep.get('classification') == 'listing_search_api' else 1,
                ep['url'],
            )
        )

        # ── SECTION 6: Data attributes seen on cards ──
        all_data_attrs = Counter()
        for scan in self.scans:
            for ci in scan.get('card_internals_sample', []):
                for attr in ci.get('data_attrs', {}).keys():
                    all_data_attrs[attr] += 1

        report['card_data_attributes'] = [
            {'attribute': attr, 'frequency': count}
            for attr, count in all_data_attrs.most_common(20)
        ]

        # ── SECTION 7: Raw card HTML samples ──
        report['card_html_samples'] = self.card_html_samples

        # ── SECTION 8: Human-readable scraper recipe ──
        report['pagination_analysis'] = _analyse_pagination(
            report['meta']['urls_visited'],
            report['api_endpoints'],
        )
        report['scraper_recipe'] = _build_recipe(report)

        return report

    # ── Save report ────────────────────────────────────────────────

    def save_report(self, report):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        label = CONFIG['site_label']
        out_dir = CONFIG['output_dir']
        os.makedirs(out_dir, exist_ok=True)

        # Full JSON report
        json_path = os.path.join(out_dir, f"site_report_{label}_{ts}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # Human-readable summary
        txt_path = os.path.join(out_dir, f"site_report_{label}_{ts}.txt")
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(_render_text_report(report))

        # Raw HTML card samples (separate file — easier to inspect)
        html_path = os.path.join(out_dir, f"card_samples_{label}_{ts}.html")
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write("<html><body style='font-family:monospace;font-size:12px;'>\n")
            f.write(f"<h2>Card HTML Samples — {label} — {ts}</h2>\n")
            for i, s in enumerate(report['card_html_samples'], 1):
                f.write(f"<h3>Sample {i} (from: {s['captured_from']})</h3>\n")
                f.write(f"<p><strong>Selector used:</strong> {s['selector_used']}</p>\n")
                if s.get('property_id') or s.get('best_link'):
                    f.write(
                        f"<p><strong>Property ID:</strong> {s.get('property_id', 'N/A')}<br>"
                        f"<strong>Best link:</strong> {s.get('best_link', 'N/A')}</p>\n"
                    )
                if s.get('preview_text'):
                    preview = s['preview_text'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    f.write(f"<p><strong>Preview:</strong> {preview}</p>\n")
                escaped = s['html'].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                f.write(f"<pre>{escaped}</pre><hr>\n")
            f.write("</body></html>")

        return json_path, txt_path, html_path

    # ── Main ───────────────────────────────────────────────────────

    def run(self):
        self.setup_browser()

        print("=" * 65)
        print("  SITE EXPLORER — Diagnostic Mode")
        print("=" * 65)
        print(f"""
HOW TO USE:
  1. Navigate to a property SEARCH RESULTS page
     e.g. https://www.rightmove.co.uk/property-for-sale/London.html
          https://www.zoopla.co.uk/for-sale/property/london/

  2. Handle cookie banners, CAPTCHAs etc. yourself

  3. Just scroll through the results — the script maps the page
     structure in the background every {CONFIG['scan_interval']}s

  4. Paginate to a 2nd or 3rd page too if you can

  5. Press Ctrl+C when done — report saves automatically

The report tells you exactly which selectors to use in your real scraper.
""")

        self.running = True
        t = threading.Thread(target=self.scan_loop, daemon=True)
        t.start()

        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n\n⏹  Stopped. Generating report...")
        finally:
            self.running = False
            report = self.generate_report()
            json_path, txt_path, html_path = self.save_report(report)

            print("\n" + "=" * 65)
            print("✅  REPORT SAVED")
            print("=" * 65)
            print(f"  📄 Full JSON:    {json_path}")
            print(f"  📝 Summary TXT:  {txt_path}")
            print(f"  🌐 Card HTML:    {html_path}")
            print(f"\n  Cards seen:  {len(self.seen_card_hashes)}")
            print(f"  URLs visited: {len(self.url_history)}")
            print(f"  API calls captured: {len(self.network_logs)}")
            print("=" * 65)

            if self.driver:
                self.driver.quit()


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _normalise_space(text):
    return re.sub(r'\s+', ' ', (text or '')).strip()


def _extract_visible_texts(elements):
    return list(dict.fromkeys(
        _normalise_space(el.text)[:240]
        for el in elements
        if _normalise_space(el.text)
    ))


def _looks_like_price(text):
    return bool(re.search(r'£\s*[\d,]+', text or ''))


def _looks_like_address(text):
    if not text or len(text) < 6:
        return False
    noisy_labels = (
        'FEATURED PROPERTY',
        'NEW HOME',
        'REDUCED',
        'PREMIUM LISTING',
    )
    if any(label in text.upper() for label in noisy_labels):
        return False
    return ',' in text or bool(UK_POSTCODE_RE.search(text))


def _extract_bedrooms(text):
    match = BEDROOM_TEXT_RE.search(text or '')
    return int(match.group(1)) if match else None


def _canonical_listing_href(href):
    return (href or '').split('#', 1)[0]


def _is_listing_href(href):
    href = href or ''
    if 'contactBranch' in href:
        return False
    return any(path in href for path in (
        '/properties/',
        '/for-sale/details/',
        '/to-rent/details/',
    ))


def _extract_property_id(text):
    if not text:
        return None
    match = (
        re.search(r'/properties/(\d{6,})', text)
        or re.search(r'propertyId=(\d{6,})', text)
        or re.search(r'"id"\s*:\s*(\d{6,})', text)
    )
    return match.group(1) if match else None


def _normalise_postcode(value):
    cleaned = _normalise_space(value).upper().replace(' ', '')
    if len(cleaned) <= 3:
        return cleaned
    return f"{cleaned[:-3]} {cleaned[-3:]}"


def _normalise_source_match(name, match):
    if isinstance(match, tuple):
        cleaned = tuple(_normalise_space(part) for part in match if _normalise_space(part))
        if not cleaned:
            return None
        return str(cleaned)

    value = _normalise_space(str(match))
    if not value:
        return None
    if name == 'postcode':
        found = UK_POSTCODE_RE.search(value)
        return _normalise_postcode(found.group(0)) if found else None
    return value


def _classify_endpoint(url, site_label):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if site_label == 'rightmove':
        if host in ('www.rightmove.co.uk', 'rightmove.co.uk'):
            if path == '/api/property-search/listing/search':
                return 'listing_search_api'
            if path.startswith('/api/property-search/'):
                return 'property_search_api'
            if path.startswith('/api/'):
                return 'first_party_api'
        return None

    if path.startswith('/api/') or path.endswith('.json'):
        return 'first_party_api'
    return None


def _extract_index_from_url(url):
    try:
        query = parse_qs(urlparse(url).query)
    except Exception:
        return None
    values = query.get('index')
    if not values:
        return None
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return None


def _analyse_pagination(url_history, api_endpoints):
    page_indices = sorted({
        idx for idx in (_extract_index_from_url(url) for url in url_history) if idx is not None
    })
    api_indices = sorted({
        idx for idx in (
            _extract_index_from_url(ep['url'])
            for ep in api_endpoints
            if ep.get('classification') == 'listing_search_api'
        )
        if idx is not None
    })
    all_indices = sorted(set(page_indices + api_indices))
    step_sizes = sorted({
        right - left
        for left, right in zip(all_indices, all_indices[1:])
        if right > left
    })
    likely_page_size = step_sizes[0] if len(step_sizes) == 1 else None

    return {
        'page_indices_seen': page_indices,
        'api_indices_seen': api_indices,
        'step_sizes_seen': step_sizes,
        'likely_page_size': likely_page_size,
    }


def _pattern_verdict(name, samples, unique_count=None):
    """Give a plain English verdict on how useful a pattern is."""
    if not samples:
        return "NOT FOUND — pattern not present in page source"
    count = unique_count if unique_count is not None else len(samples)
    if name in ('lat_lng_1', 'lat_lng_2', 'lat_lng_3', 'lat_lng_4'):
        return f"✅ COORDINATES FOUND ({count} unique matches) — can extract lat/lng directly from source"
    if name == 'postcode':
        return f"✅ UK POSTCODES FOUND ({count} unique matches) — useful for validation/geocoding"
    if name == 'display_address':
        return f"✅ ADDRESS IN JSON ({count} unique matches) — cleaner than DOM scraping"
    if name in ('price_json', 'json_api_price'):
        return f"✅ PRICE IN JSON ({count} unique matches) — more reliable than CSS selector"
    if name == 'listing_id':
        return f"ℹ️  LISTING IDs FOUND ({count}) — useful for dedup and API calls"
    return f"ℹ️  {count} unique matches found"


def _build_recipe(report):
    """
    Build a plain-English scraper recipe from the findings.
    This is the most actionable part of the report.
    """
    lines = []
    lines.append("RECOMMENDED SCRAPER RECIPE")
    lines.append("=" * 50)

    apis = report.get('api_endpoints', [])
    listing_apis = [a for a in apis if a.get('classification') == 'listing_search_api']
    pagination = report.get('pagination_analysis', {})
    sp = report.get('source_patterns', {})

    if listing_apis:
        lines.append("\n1. PREFER THE FIRST-PARTY SEARCH API:")
        lines.append(f"   {listing_apis[0]['url']}")
        if pagination.get('likely_page_size'):
            lines.append(
                f"   Pagination signal: index increments by {pagination['likely_page_size']}"
            )
        elif pagination.get('step_sizes_seen'):
            lines.append(f"   Index steps seen: {pagination['step_sizes_seen']}")
        lines.append("   Use this as the primary extraction path; keep DOM scraping as fallback.")
    else:
        lines.append("\n1. API PATH: ⚠ No first-party listing API captured in this run")

    cards = report.get('card_selectors', [])
    if cards:
        best = cards[0]
        lines.append(f"\n2. DOM FALLBACK CARD SELECTOR:")
        lines.append(f"   {best['selector']}")
        lines.append(
            f"   Score={best['max_score']} | max_count={best['max_cards_found']} "
            f"| sample_property_ids={best['max_unique_property_ids_in_sample']}"
        )
    else:
        lines.append("\n2. CARD SELECTOR: ⚠ None found reliably — inspect card_html_samples manually")

    prices = report.get('field_selectors', {}).get('price', [])
    if prices:
        best_p = prices[0]
        lines.append(f"\n3. EXTRACT PRICE WITH:")
        lines.append(f"   {best_p['selector']}")
        lines.append(f"   Samples: {best_p['samples']}")
    elif sp.get('json_api_price'):
        lines.append(f"\n3. EXTRACT PRICE FROM PAGE JSON:")
        lines.append('   Pattern: "price":{"amount":...}')
        lines.append(f"   Samples: {sp['json_api_price']['samples'][:3]}")

    addrs = report.get('field_selectors', {}).get('address', [])
    if addrs:
        best_a = addrs[0]
        lines.append(f"\n4. EXTRACT ADDRESS WITH:")
        lines.append(f"   {best_a['selector']}")
        lines.append(f"   Samples: {best_a['samples']}")
    elif sp.get('display_address'):
        lines.append(f"\n4. EXTRACT ADDRESS FROM JSON IN PAGE SOURCE:")
        lines.append(f'   Pattern: "displayAddress":"([^"]+)"')
        lines.append(f"   Samples: {sp['display_address']['samples'][:3]}")

    links = report.get('field_selectors', {}).get('link', [])
    if links:
        best_l = links[0]
        lines.append(f"\n5. EXTRACT LISTING URL WITH:")
        lines.append(f"   {best_l['selector']}")
        lines.append(f"   Samples: {best_l['samples']}")

    coord_keys = ['lat_lng_1', 'lat_lng_2', 'lat_lng_3', 'lat_lng_4']
    coord_found = [
        (k, sp[k])
        for k in coord_keys
        if k in sp and sp[k]['max_matches_in_single_scan'] > 0
    ]
    if coord_found:
        best_c = coord_found[0]
        lines.append(f"\n6. EXTRACT COORDINATES FROM PAGE SOURCE:")
        lines.append(f"   Pattern: {SOURCE_PATTERNS[best_c[0]]}")
        lines.append(
            f"   Up to {best_c[1]['max_matches_in_single_scan']} matches in one scan "
            f"({best_c[1]['unique_matches']} unique samples saved)"
        )
    else:
        lines.append(f"\n6. COORDINATES: ⚠ Not found in page source — may need geocoding by postcode instead")

    if sp.get('postcode', {}).get('unique_matches', 0) > 0:
        lines.append(f"\n7. POSTCODES: ✅ Strict UK postcode pattern present in source:")
        lines.append(f"   Pattern: {SOURCE_PATTERNS['postcode']}")
        lines.append(f"   Samples: {sp['postcode']['samples'][:4]}")

    if apis:
        lines.append(f"\n8. FIRST-PARTY API ENDPOINTS CAPTURED ({len(apis)}):")
        for a in apis[:5]:
            lines.append(f"   • [{a['status']}] {a['classification']} — {a['url']}")

    pag = report.get('pagination', [])
    if pag or pagination.get('likely_page_size') or pagination.get('page_indices_seen'):
        lines.append(f"\n9. PAGINATION:")
        if pagination.get('page_indices_seen'):
            lines.append(f"   Page URLs carried indices: {pagination['page_indices_seen']}")
        if pagination.get('api_indices_seen'):
            lines.append(f"   Listing API indices: {pagination['api_indices_seen']}")
        if pagination.get('likely_page_size'):
            lines.append(f"   Likely page size: {pagination['likely_page_size']}")
        if pag:
            best_pg = pag[0]
            lines.append(f"   DOM selector: {best_pg['selector']}")
            lines.append(f"   DOM next href: {best_pg.get('href_sample', 'N/A')}")

    data_attrs = report.get('card_data_attributes', [])
    if data_attrs:
        lines.append(f"\n10. USEFUL TOP-LEVEL DATA-* ATTRIBUTES ON CARDS:")
        for da in data_attrs[:6]:
            lines.append(f"   • {da['attribute']} (seen {da['frequency']}x)")

    lines.append("\n\nSee card_html_samples_*.html for raw HTML to inspect manually.")
    return "\n".join(lines)


def _render_text_report(report):
    """Render the full report as readable plain text."""
    lines = []
    meta = report['meta']
    lines.append("=" * 65)
    lines.append(f"SITE EXPLORER REPORT — {meta['site_label'].upper()}")
    lines.append(f"Generated: {meta['generated_at']}")
    lines.append(f"Session:   {meta['session_minutes']} minutes")
    lines.append(f"Scans:     {meta['total_scans']}")
    lines.append(f"Cards seen:{meta['unique_cards_seen']}")
    lines.append("=" * 65)

    lines.append("\nURLs VISITED:")
    for u in meta['urls_visited']:
        lines.append(f"  {u}")

    lines.append("\n" + "─" * 65)
    lines.append(report['scraper_recipe'])

    lines.append("\n" + "─" * 65)
    lines.append("\nALL CARD SELECTORS TRIED:")
    for s in report['card_selectors']:
        flag = "✅" if s['recommended'] else "  "
        lines.append(
            f"  {flag} {s['selector']:<45} "
            f"score={s['max_score']:<3} max={s['max_cards_found']:<4} "
            f"ids={s['max_unique_property_ids_in_sample']:<2} seen_in={s['appeared_in_n_scans']}"
        )

    lines.append("\n" + "─" * 65)
    lines.append("\nSOURCE PATTERN ANALYSIS:")
    for name, data in report['source_patterns'].items():
        lines.append(f"\n  [{name}]")
        lines.append(f"   Regex:   {data['regex']}")
        lines.append(f"   Verdict: {data['verdict']}")
        lines.append(
            f"   Unique:  {data['unique_matches']} | "
            f"Max/scan: {data['max_matches_in_single_scan']} | "
            f"Seen in scans: {data['seen_in_n_scans']}"
        )
        lines.append(f"   Samples: {data['samples']}")

    lines.append("\n" + "─" * 65)
    lines.append("\nPAGINATION ANALYSIS:")
    pagination = report.get('pagination_analysis', {})
    if pagination:
        lines.append(f"  Page URL indices: {pagination.get('page_indices_seen', [])}")
        lines.append(f"  API indices:      {pagination.get('api_indices_seen', [])}")
        lines.append(f"  Step sizes:       {pagination.get('step_sizes_seen', [])}")
        lines.append(f"  Likely page size: {pagination.get('likely_page_size')}")

    lines.append("\n" + "─" * 65)
    lines.append("\nFIRST-PARTY API ENDPOINTS CAPTURED:")
    if report['api_endpoints']:
        for ep in report['api_endpoints']:
            lines.append(f"  [{ep['status']}] {ep['classification']:<20} {ep['url']}")
    else:
        lines.append("  None captured")

    lines.append("\n" + "─" * 65)
    lines.append("\nCARD TOP-LEVEL data-* ATTRIBUTES:")
    for da in report['card_data_attributes']:
        lines.append(f"  {da['attribute']:<40} x{da['frequency']}")

    lines.append("\n" + "=" * 65)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    explorer = SiteExplorer()
    explorer.run()
