"""Resolve coordinates for seed/curated-places.json.

Why this script exists
----------------------
Three naive approaches were tried first and all failed:

1. Nominatim free-text search on the *restaurant name* ("Amber, Hong Kong")
   returns nothing. Nominatim geocodes addresses, not POI trade names.
2. Overpass name matching finds only ~20/97 and silently picks the *wrong*
   branch when a chain has several (Duddell's matched its airport outlet;
   "Amber" matched an unrelated shop in Tai Hang).
3. Wikidata SPARQL and guide.michelin.com (403) yield nothing usable.

What works is a two-step route, which is what this script implements:

  name -> venue (hotel / building / street address), from published sources
  venue -> coordinates, via Nominatim (which handles addresses and hotels well)

Every venue below is transcribed from a named source URL; nothing is guessed.
Results are then checked three ways:

  * bounding box (HK / Macau) -- anything outside is dropped
  * reverse geocoding -- the returned administrative area must be consistent
    with the district the source claims; mismatches are dropped
  * OSM corroboration -- where an exact-name OSM POI sits within 500 m of the
    geocoded venue, the two independent sources agree and we upgrade the point
    to that POI (the restaurant itself rather than the building centroid)

Anything that does not survive all three gets lat/lng = null. A blank is
better than a wrong pin.

Usage:  uv run python tools/resolve_curated.py [--apply]
Without --apply it only prints the verification table.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed" / "curated-places.json"
CACHE = ROOT / ".local" / "geocode-cache.json"

UA = "harbor-lantern/0.3 (+https://github.com/currentJob/harbor-lantern)"
NOMINATIM = "https://nominatim.openstreetmap.org"
OVERPASS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
RATE_LIMIT_S = 1.1  # Nominatim usage policy: <= 1 request/second

# Hard geographic bounds. Anything outside is definitionally wrong.
BBOX = {
    "HK": (22.15, 22.58, 113.82, 114.44),
    "MO": (22.10, 22.22, 113.52, 113.60),
}

# ---------------------------------------------------------------------------
# Source URLs
# ---------------------------------------------------------------------------
WIKI = "https://en.wikipedia.org/wiki/List_of_Michelin-starred_restaurants_in_Hong_Kong_and_Macau"
MACAONEWS = "https://macaonews.org/food-drink/michelin-guide-2026-hong-kong-macao-stars/"
FOODIE = "https://www.afoodieworld.com/blog/2026/03/19/michelin-guide-hk-2026-list/"
GLP = "https://www.grandlisboapalace.com/en/glp-michelin-awards-2026"

# ---------------------------------------------------------------------------
# Venue table: (name, city) -> where the restaurant actually is.
#
#   kind "address" -> we have the restaurant's own street address; geocoding it
#                     yields coord_confidence "verified".
#   kind "hotel"   -> we only know the containing hotel/building; geocoding that
#                     yields coord_confidence "hotel" (building-level accuracy).
#
# "query" is what we hand to Nominatim. "district" is the administrative area we
# expect back from reverse geocoding.
# ---------------------------------------------------------------------------
Venue = dict

VENUES: list[Venue] = [
    # --- Hong Kong, three stars -------------------------------------------
    dict(name="8½ Otto e Mezzo – Bombana", city="HK", kind="hotel",
         address="Shop 202, Alexandra House, 18 Chater Road, Central, Hong Kong",
         query="Alexandra House, Chater Road, Central, Hong Kong",
         district="Central", source=WIKI),
    dict(name="Amber", city="HK", kind="hotel",
         address="7/F, The Landmark Mandarin Oriental, 15 Queen's Road Central, Central, Hong Kong",
         query="The Landmark Mandarin Oriental Hong Kong",
         district="Central", source=WIKI),
    dict(name="Caprice", city="HK", kind="hotel",
         address="6/F, Four Seasons Hotel Hong Kong, 8 Finance Street, Central, Hong Kong",
         query="Four Seasons Hotel Hong Kong",
         district="Central", source=WIKI),
    dict(name="Forum", city="HK", kind="hotel",
         address="1/F, Sino Plaza, 255 Gloucester Road, Causeway Bay, Hong Kong",
         query="Sino Plaza, Hong Kong",
         district="Causeway Bay", source=WIKI),
    dict(name="Sushi Shikon", city="HK", kind="hotel",
         address="7/F, The Landmark Mandarin Oriental, 15 Queen's Road Central, Central, Hong Kong",
         query="The Landmark Mandarin Oriental Hong Kong",
         district="Central", source=WIKI),
    dict(name="T'ang Court", city="HK", kind="hotel",
         address="1/F, The Langham Hong Kong, 8 Peking Road, Tsim Sha Tsui, Hong Kong",
         query="The Langham Hong Kong",
         district="Tsim Sha Tsui", source=WIKI),
    dict(name="Ta Vie", city="HK", kind="hotel",
         address="2/F, The Pottinger Hong Kong, 74 Queen's Road Central, Central, Hong Kong",
         query="The Pottinger Hong Kong",
         district="Central", source=WIKI),

    # --- Hong Kong, two stars ---------------------------------------------
    dict(name="Arbor", city="HK", kind="address",
         address="25/F, H Queen's, 80 Queen's Road Central, Central, Hong Kong",
         query="80 Queen's Road Central, Central, Hong Kong",
         district="Central",
         source="https://www.openrice.com/en/hongkong/r-arbor-central-western-fine-dining-r569060"),
    dict(name="Bo Innovation", city="HK", kind="address",
         address="1/F, H Code, 45 Pottinger Street, Central, Hong Kong",
         query="45 Pottinger Street, Central, Hong Kong",
         district="Central", source="https://www.boinnovation.com/"),
    dict(name="Cristal Room by Anne-Sophie Pic", city="HK", kind="hotel",
         address="The Landmark Atrium, 15 Queen's Road Central, Central, Hong Kong",
         query="The Landmark, Queen's Road Central, Central, Hong Kong",
         district="Central", source=WIKI),
    dict(name="L'Atelier de Joel Robuchon", city="HK", kind="hotel",
         address="Shop 401, The Landmark Atrium, 15 Queen's Road Central, Central, Hong Kong",
         query="The Landmark, Queen's Road Central, Central, Hong Kong",
         district="Central", source=WIKI),
    dict(name="L'Envol", city="HK", kind="hotel",
         address="4/F, The St. Regis Hong Kong, 1 Harbour Drive, Wan Chai, Hong Kong",
         query="The St. Regis Hong Kong",
         district="Wan Chai", source=WIKI),
    dict(name="Lai Ching Heen", city="HK", kind="hotel",
         address="Regent Hong Kong, 18 Salisbury Road, Tsim Sha Tsui, Hong Kong",
         query="Regent Hong Kong",
         district="Tsim Sha Tsui", source=WIKI),
    dict(name="Lung King Heen", city="HK", kind="hotel",
         address="4/F, Four Seasons Hotel Hong Kong, 8 Finance Street, Central, Hong Kong",
         query="Four Seasons Hotel Hong Kong",
         district="Central", source=WIKI),
    dict(name="Noi by Paulo Airaudo", city="HK", kind="hotel",
         address="5/F, Four Seasons Hotel Hong Kong, 8 Finance Street, Central, Hong Kong",
         query="Four Seasons Hotel Hong Kong", district="Central",
         source="https://www.openrice.com/en/hongkong/r-noi-by-paulo-airaudo-central-italian-fine-dining-r769820"),
    dict(name="Octavium", city="HK", kind="address",
         address="8/F, One Chinachem Central, 22 Des Voeux Road Central, Central, Hong Kong",
         query="22 Des Voeux Road Central, Central, Hong Kong", district="Central",
         source="https://www.openrice.com/en/hongkong/r-octavium-italian-restaurant-central-italian-fine-dining-r550790"),
    dict(name="Rùn", city="HK", kind="hotel",
         address="The St. Regis Hong Kong, 1 Harbour Drive, Wan Chai, Hong Kong",
         query="The St. Regis Hong Kong",
         district="Wan Chai", source=WIKI),
    dict(name="TATE Dining Room", city="HK", kind="address",
         address="210 Hollywood Road, Sheung Wan, Hong Kong",
         query="210 Hollywood Road, Sheung Wan, Hong Kong", district="Sheung Wan",
         source="https://www.openrice.com/en/hongkong/r-tate-dining-room-sheung-wan-french-fine-dining-r518192"),
    dict(name="Tin Lung Heen", city="HK", kind="hotel",
         address="102/F, The Ritz-Carlton Hong Kong, ICC, 1 Austin Road West, West Kowloon, Hong Kong",
         query="The Ritz-Carlton Hong Kong",
         district="Tsim Sha Tsui", source=WIKI),
    dict(name="Ying Jee Club", city="HK", kind="address",
         address="G/F-1/F, Nexxus Building, 41 Connaught Road Central, Central, Hong Kong",
         query="Nexxus Building, Hong Kong",
         district="Central", source=WIKI),

    # --- Hong Kong, one star ----------------------------------------------
    dict(name="Ami", city="HK", kind="address",
         address="Shop 302, 3/F, Alexandra House, 16-20 Chater Road, Central, Hong Kong",
         query="Alexandra House, Chater Road, Central, Hong Kong", district="Central",
         source="https://www.openrice.com/en/hongkong/r-ami-central-french-fine-dining-r722326"),
    dict(name="Andō", city="HK", kind="address",
         address="1/F, Somptueux Central, 52 Wellington Street, Central, Hong Kong",
         query="52 Wellington Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Arcane", city="HK", kind="address",
         address="3/F, 18 On Lan Street, Central, Hong Kong",
         query="18 On Lan Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Beefbar", city="HK", kind="address",
         address="2/F, Club Lusitano, 16 Ice House Street, Central, Hong Kong",
         query="16 Ice House Street, Central, Hong Kong", district="Central",
         source="https://www.openrice.com/en/hongkong/r-beefbar-hong-kong-central-western-fine-dining-r477882"),
    dict(name="Belon", city="HK", kind="address",
         address="1-5 Elgin Street, Central, Hong Kong",
         query="1 Elgin Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Chaat", city="HK", kind="hotel",
         address="5/F, Rosewood Hong Kong, 18 Salisbury Road, Tsim Sha Tsui, Hong Kong",
         query="Rosewood Hong Kong", district="Tsim Sha Tsui", source=WIKI),
    dict(name="China Tang", city="HK", kind="hotel",
         address="Shop 411-413, The Landmark Atrium, 15 Queen's Road Central, Central, Hong Kong",
         query="The Landmark, Queen's Road Central, Central, Hong Kong",
         district="Central", source=FOODIE),
    dict(name="Duddell's", city="HK", kind="address",
         address="Level 3, Shanghai Tang Mansion, 1 Duddell Street, Central, Hong Kong",
         query="1 Duddell Street, Central, Hong Kong", district="Central",
         source="https://www.discoverhongkong.com/eng/place-to-go/travel.guide-duddell-s.html"),
    dict(name="Estro", city="HK", kind="address",
         address="2/F, 1 Duddell Street, Central, Hong Kong",
         query="1 Duddell Street, Central, Hong Kong", district="Central",
         source="https://www.openrice.com/en/hongkong/r-estro-central-italian-fine-dining-r731074"),
    dict(name="Feuille", city="HK", kind="address",
         address="198 Wellington Street, Central, Hong Kong",
         query="198 Wellington Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Fook Lam Moon", city="HK", kind="address",
         address="Shop 3, G/F, Newman House, 35-45 Johnston Road, Wan Chai, Hong Kong",
         query="35 Johnston Road, Wan Chai, Hong Kong", district="Wan Chai",
         source="https://www.openrice.com/en/hongkong/r-fook-lam-moon-wan-chai-guangdong-dim-sum-r812"),
    dict(name="Fu Ho", city="HK", kind="hotel",
         address="Shop 402, 4/F, Mira Place One, 132 Nathan Road, Tsim Sha Tsui, Hong Kong",
         query="Mira Place, Nathan Road, Hong Kong", district="Tsim Sha Tsui",
         source="https://www.openrice.com/en/hongkong/r-fu-ho-restaurant-tsim-sha-tsui-guangdong-fine-dried-seafood-r9577"),
    dict(name="Gaddi's", city="HK", kind="hotel",
         address="1/F, The Peninsula Hong Kong, Salisbury Road, Tsim Sha Tsui, Hong Kong",
         query="The Peninsula Hong Kong", district="Tsim Sha Tsui", source=WIKI),
    dict(name="Godenya", city="HK", kind="address",
         address="UG/F, 182 Wellington Street, Sheung Wan, Hong Kong",
         query="182 Wellington Street, Hong Kong", district="Sheung Wan",
         source="https://www.openrice.com/en/hongkong/r-godenya-sheung-wan-japanese-omakase-r664928"),
    dict(name="Hansik Goo", city="HK", kind="address",
         address="1/F, The Wellington, 198 Wellington Street, Central, Hong Kong",
         query="198 Wellington Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Ho Hung Kee", city="HK", kind="address",
         address="Shop 1204-1205, 12/F, Hysan Place, 500 Hennessy Road, Causeway Bay, Hong Kong",
         query="Hysan Place, Hennessy Road, Causeway Bay, Hong Kong", district="Causeway Bay",
         source="https://www.openrice.com/en/hongkong/r-ho-hung-kee-causeway-bay-guangdong-congee-r114105"),
    dict(name="I M Teppanyaki & Wine", city="HK", kind="address",
         address="1/F, SL Ginza, 68 Electric Road, Tin Hau, Hong Kong",
         query="68 Electric Road, Tin Hau, Hong Kong", district="Tin Hau",
         source="https://www.openrice.com/en/hongkong/r-i-m-teppanyaki-and-wine-tin-hau-japanese-teppanyaki-r714407"),
    dict(name="Imperial Treasure Fine Chinese Cuisine", city="HK", kind="address",
         address="10/F, One Peking, 1 Peking Road, Tsim Sha Tsui, Hong Kong",
         query="One Peking, Hong Kong", district="Tsim Sha Tsui",
         source="https://www.openrice.com/en/hongkong/r-imperial-treasure-fine-chinese-cuisine-tsim-sha-tsui-guangdong-r518605"),
    dict(name="Kam's Roast Goose", city="HK", kind="address",
         address="G/F, 226 Hennessy Road, Wan Chai, Hong Kong",
         query="226 Hennessy Road, Wan Chai, Hong Kong", district="Wan Chai", source=WIKI),
    dict(name="Kappo Rin", city="HK", kind="hotel",
         address="The Landmark Mandarin Oriental, 15 Queen's Road Central, Central, Hong Kong",
         query="The Landmark Mandarin Oriental Hong Kong", district="Central", source=WIKI),
    dict(name="Liu Yuan Pavilion", city="HK", kind="address",
         address="3/F, The Broadway, 54-62 Lockhart Road, Wan Chai, Hong Kong",
         query="54 Lockhart Road, Wan Chai, Hong Kong", district="Wan Chai", source=WIKI),
    dict(name="Loaf On", city="HK", kind="address",
         address="49 See Cheung Street, Sai Kung, Hong Kong",
         query="49 See Cheung Street, Sai Kung, Hong Kong", district="Sai Kung",
         source="https://loafon.com/en/contact.php"),
    dict(name="Louise", city="HK", kind="address",
         address="G/F, PMQ, 35 Aberdeen Street, Central, Hong Kong",
         query="PMQ, Aberdeen Street, Central, Hong Kong", district="Central",
         source="https://www.pmq.org.hk/shop/louise/"),
    dict(name="Man Ho", city="HK", kind="hotel",
         address="3/F, JW Marriott Hotel Hong Kong, Pacific Place, 88 Queensway, Admiralty, Hong Kong",
         query="JW Marriott Hotel Hong Kong", district="Admiralty", source=WIKI),
    dict(name="Man Wah", city="HK", kind="hotel",
         address="25/F, Mandarin Oriental Hong Kong, 5 Connaught Road Central, Central, Hong Kong",
         query="Mandarin Oriental Hong Kong", district="Central", source=WIKI),
    dict(name="Mono", city="HK", kind="address",
         address="5/F, 18 On Lan Street, Central, Hong Kong",
         query="18 On Lan Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Mora", city="HK", kind="address",
         address="40 Upper Lascar Row, Sheung Wan, Hong Kong",
         query="40 Upper Lascar Row, Sheung Wan, Hong Kong", district="Sheung Wan",
         source="https://www.mora.com.hk/"),
    dict(name="Nagamoto", city="HK", kind="address",
         address="18 On Lan Street, Central, Hong Kong",
         query="18 On Lan Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Neighborhood", city="HK", kind="address",
         address="G/F, 61-63 Hollywood Road, Central, Hong Kong",
         query="61 Hollywood Road, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="New Punjab Club", city="HK", kind="address",
         address="G/F, World Wide Commercial Building, 34 Wyndham Street, Central, Hong Kong",
         query="34 Wyndham Street, Central, Hong Kong", district="Central",
         source="https://www.newpunjabclub.com/"),
    dict(name="Pang's Kitchen", city="HK", kind="address",
         address="25 Yik Yam Street, Happy Valley, Hong Kong",
         query="25 Yik Yam Street, Happy Valley, Hong Kong", district="Happy Valley", source=WIKI),
    dict(name="Petrus", city="HK", kind="hotel",
         address="56/F, Island Shangri-La, Pacific Place, Supreme Court Road, Admiralty, Hong Kong",
         query="Island Shangri-La Hong Kong", district="Admiralty", source=WIKI),
    dict(name="Plaisance by Mauro Colagreco", city="HK", kind="address",
         address="1 Duddell Street, Central, Hong Kong",
         query="1 Duddell Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Racines", city="HK", kind="address",
         address="G/F, 22 Upper Station Street, Sheung Wan, Hong Kong",
         query="22 Upper Station Street, Sheung Wan, Hong Kong", district="Sheung Wan",
         source="https://guide.michelin.com/en/hong-kong-region/hong-kong/restaurant/racines-1210713"),
    dict(name="Roganic", city="HK", kind="address",
         address="Sino Plaza, 255 Gloucester Road, Causeway Bay, Hong Kong",
         query="Sino Plaza, Hong Kong",
         district="Causeway Bay", source=WIKI),
    dict(name="Ryota Kappou Modern", city="HK", kind="address",
         address="18 On Lan Street, Central, Hong Kong",
         query="18 On Lan Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Seventh Son", city="HK", kind="hotel",
         address="3/F, Wharney Hotel, 57-73 Lockhart Road, Wan Chai, Hong Kong",
         query="Wharney Hotel Hong Kong, Lockhart Road, Wan Chai", district="Wan Chai", source=WIKI),
    dict(name="Shang Palace", city="HK", kind="hotel",
         address="B2, Kowloon Shangri-La, 64 Mody Road, Tsim Sha Tsui East, Hong Kong",
         query="Kowloon Shangri-La", district="Tsim Sha Tsui", source=WIKI),
    dict(name="Spring Moon", city="HK", kind="hotel",
         address="1/F, The Peninsula Hong Kong, Salisbury Road, Tsim Sha Tsui, Hong Kong",
         query="The Peninsula Hong Kong", district="Tsim Sha Tsui", source=WIKI),
    dict(name="Summer Palace", city="HK", kind="hotel",
         address="5/F, Island Shangri-La, Pacific Place, Supreme Court Road, Admiralty, Hong Kong",
         query="Island Shangri-La Hong Kong", district="Admiralty", source=WIKI),
    dict(name="Sun Tung Lok", city="HK", kind="hotel",
         address="Shop 401, 4/F, Mira Place One, 132 Nathan Road, Tsim Sha Tsui, Hong Kong",
         query="Mira Place, Nathan Road, Hong Kong", district="Tsim Sha Tsui",
         source="https://www.openrice.com/en/hongkong/r-sun-tung-lok-chinese-cuisine-tsim-sha-tsui-guangdong-fine-dried-seafood-r39725"),
    dict(name="Sushi Takeshi", city="HK", kind="hotel",
         address="The Mira Hong Kong, 118 Nathan Road, Tsim Sha Tsui, Hong Kong",
         query="The Mira Hong Kong", district="Tsim Sha Tsui", source=FOODIE),
    dict(name="Sushi Wadatsumi", city="HK", kind="hotel",
         address="K11 MUSEA, 18 Salisbury Road, Tsim Sha Tsui, Hong Kong",
         query="K11 MUSEA", district="Tsim Sha Tsui", source=WIKI),
    dict(name="The Chairman", city="HK", kind="address",
         address="18 Kau U Fong, Central, Hong Kong",
         query="18 Kau U Fong, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="The Legacy House", city="HK", kind="hotel",
         address="5/F, Rosewood Hong Kong, 18 Salisbury Road, Tsim Sha Tsui, Hong Kong",
         query="Rosewood Hong Kong", district="Tsim Sha Tsui", source=WIKI),
    dict(name="Tosca di Angelo", city="HK", kind="hotel",
         address="102/F, The Ritz-Carlton Hong Kong, ICC, 1 Austin Road West, West Kowloon, Hong Kong",
         query="The Ritz-Carlton Hong Kong", district="Tsim Sha Tsui", source=WIKI),
    dict(name="Tuber Umberto Bombana", city="HK", kind="hotel",
         address="K11 MUSEA, 18 Salisbury Road, Tsim Sha Tsui, Hong Kong",
         query="K11 MUSEA", district="Tsim Sha Tsui", source=WIKI),
    dict(name="Vea", city="HK", kind="address",
         address="29-30/F, The Wellington, 198 Wellington Street, Central, Hong Kong",
         query="198 Wellington Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Whey", city="HK", kind="address",
         address="198 Wellington Street, Central, Hong Kong",
         query="198 Wellington Street, Central, Hong Kong", district="Central", source=WIKI),
    dict(name="Xin Rong Ji", city="HK", kind="hotel",
         address="G/F-1/F, China Overseas Building, 138 Lockhart Road, Wan Chai, Hong Kong",
         query="China Overseas Building, Lockhart Road, Hong Kong", district="Wan Chai",
         source="https://www.openrice.com/en/hongkong/r-xinrongji-wan-chai-jiang-zhe-r560851"),
    dict(name="Yardbird", city="HK", kind="address",
         address="154-158 Wing Lok Street, Sheung Wan, Hong Kong",
         query="154 Wing Lok Street, Sheung Wan, Hong Kong", district="Sheung Wan",
         source="https://www.exploretock.com/yardbirdhongkong/"),
    dict(name="Yat Lok", city="HK", kind="address",
         address="G/F, Conwell House, 34-38 Stanley Street, Central, Hong Kong",
         query="34 Stanley Street, Central, Hong Kong", district="Central",
         source="https://www.openrice.com/en/hongkong/r-yat-lok-restaurant-central-hong-kong-style-chinese-bbq-r78351"),
    dict(name="Yat Tung Heen", city="HK", kind="hotel",
         address="B2, Eaton HK, 380 Nathan Road, Jordan, Hong Kong",
         query="Eaton HK, Nathan Road, Hong Kong", district="Jordan", source=WIKI),
    dict(name="Yong Fu", city="HK", kind="hotel",
         address="G/F-1/F, Golden Star Building, 20-24 Lockhart Road, Wan Chai, Hong Kong",
         query="Golden Star Building, Lockhart Road, Hong Kong", district="Wan Chai",
         source="https://www.openrice.com/en/hongkong/r-yongfu-wan-chai-jiang-zhe-seafood-r657636"),
    dict(name="Yè Shanghai", city="HK", kind="hotel",
         address="6/F, Marco Polo Hongkong Hotel, 3 Canton Road, Tsim Sha Tsui, Hong Kong",
         query="Marco Polo Hongkong Hotel", district="Tsim Sha Tsui",
         source="https://guide.michelin.com/en/hong-kong-region/hong-kong/restaurant/ye-shanghai-tsim-sha-tsui"),
    dict(name="Zhejiang Heen", city="HK", kind="address",
         address="1-3/F, ZJ 300, 300-306 Lockhart Road, Wan Chai, Hong Kong",
         query="300 Lockhart Road, Wan Chai, Hong Kong", district="Wan Chai",
         source="https://www.openrice.com/en/hongkong/r-zhejiang-heen-wan-chai-jiang-zhe-r77406"),
    dict(name="Épure", city="HK", kind="address",
         address="Shop 403, 4/F, Ocean Centre, Harbour City, Tsim Sha Tsui, Hong Kong",
         query="Ocean Centre, Hong Kong",
         district="Tsim Sha Tsui", source=WIKI),

    # --- Macau -------------------------------------------------------------
    dict(name="Jade Dragon", city="MO", kind="hotel",
         address="City of Dreams, Estrada do Istmo, Cotai, Macau",
         query="City of Dreams Macau", district="Cotai", source=WIKI),
    dict(name="Robuchon au Dôme", city="MO", kind="hotel",
         address="43/F, Grand Lisboa, Avenida de Lisboa, Macau",
         query="Grand Lisboa, Macau", district="Se", source=WIKI),
    dict(name="Alain Ducasse at Morpheus", city="MO", kind="hotel",
         address="Morpheus, City of Dreams, Estrada do Istmo, Cotai, Macau",
         query="Morpheus Hotel", district="Cotai", source=WIKI),
    dict(name="Chef Tam's Seasons", city="MO", kind="hotel",
         address="Wynn Palace, Avenida da Nave Desportiva, Cotai, Macau",
         query="Wynn Palace, Macau", district="Cotai", source=WIKI),
    dict(name="Feng Wei Ju", city="MO", kind="hotel",
         address="5/F, StarWorld Hotel, Avenida da Amizade, Macau",
         query="StarWorld Hotel Macau", district="Se", source=WIKI),
    dict(name="The Eight", city="MO", kind="hotel",
         address="2/F, Grand Lisboa, Avenida de Lisboa, Macau",
         query="Grand Lisboa, Macau", district="Se", source=WIKI),
    dict(name="The Huaiyang Garden", city="MO", kind="hotel",
         address="The Londoner Macao, Estrada do Istmo, Cotai, Macau",
         query="The Londoner Macao", district="Cotai", source=WIKI),
    dict(name="Wing Lei", city="MO", kind="hotel",
         address="Wynn Macau, Rua Cidade de Sintra, NAPE, Macau",
         query="Wynn Macau", district="Se", source=WIKI),
    dict(name="8½ Otto e Mezzo – Bombana", city="MO", kind="hotel",
         address="Galaxy Macau, Estrada da Baia de Nossa Senhora da Esperanca, Cotai, Macau",
         query="Galaxy Macau", district="Cotai", source=WIKI),
    dict(name="Aji", city="MO", kind="hotel",
         address="MGM Cotai, Avenida da Nave Desportiva, Cotai, Macau",
         query="MGM Cotai", district="Cotai", source=MACAONEWS),
    dict(name="Don Alfonso 1890", city="MO", kind="hotel",
         address="Shop 307, 3/F, Palazzo Versace, Grand Lisboa Palace, Rua do Tiro, Cotai, Macau",
         query="Grand Lisboa Palace, Macau", district="Cotai",
         source="https://guide.michelin.com/sg/en/macau-region/macau/restaurant/don-alfonso-1890-1212351"),
    dict(name="Five Foot Road", city="MO", kind="hotel",
         address="MGM Cotai, Avenida da Nave Desportiva, Cotai, Macau",
         query="MGM Cotai", district="Cotai", source=WIKI),
    dict(name="Lai Heen", city="MO", kind="hotel",
         address="51/F, The Ritz-Carlton Macau, Galaxy Macau, Cotai, Macau",
         query="The Ritz-Carlton Macau", district="Cotai", source=WIKI),
    dict(name="Mizumi", city="MO", kind="hotel",
         address="Wynn Palace, Avenida da Nave Desportiva, Cotai, Macau",
         query="Wynn Palace, Macau", district="Cotai",
         source="https://www.wynnresortsmacau.com/en/wynn-palace/dining/mizumi-wp"),
    dict(name="Palace Garden", city="MO", kind="hotel",
         address="Shop 306, 3/F, Grand Lisboa Palace, Rua do Tiro, Cotai, Macau",
         query="Grand Lisboa Palace, Macau", district="Cotai",
         source="https://guide.michelin.com/us/en/macau-region/macau/restaurant/palace-garden"),
    dict(name="Pearl Dragon", city="MO", kind="hotel",
         address="Studio City Macau, Estrada do Istmo, Cotai, Macau",
         query="Studio City Macau", district="Cotai", source=WIKI),
    dict(name="Sushi Kinetsu", city="MO", kind="hotel",
         address="City of Dreams, Estrada do Istmo, Cotai, Macau",
         query="City of Dreams Macau", district="Cotai", source=WIKI),
    dict(name="Sushi Kissho by Miyakawa", city="MO", kind="hotel",
         address="Raffles at Galaxy Macau, Estrada da Baia de Nossa Senhora da Esperanca, Cotai, Macau",
         query="Galaxy Macau", district="Cotai", source=WIKI),
    dict(name="Ying", city="MO", kind="hotel",
         address="11/F, Altira Macau, Avenida de Kwong Tung, Taipa, Macau",
         query="Altira Macau", district="Taipa", source=WIKI),
    dict(name="Zi Yat Heen", city="MO", kind="hotel",
         address="Four Seasons Hotel Macao, Estrada da Baia de Nossa Senhora da Esperanca, Cotai, Macau",
         query="Four Seasons Hotel Macao", district="Cotai", source=WIKI),
    dict(name="Zuicho", city="MO", kind="hotel",
         address="The Karl Lagerfeld, Grand Lisboa Palace, Rua do Tiro, Cotai, Macau",
         query="Grand Lisboa Palace, Macau", district="Cotai", source=GLP),
]

# District aliases: what reverse geocoding may legitimately call each district.
DISTRICT_ALIASES = {
    "Central": ["central", "central and western", "中西區", "sheung wan", "soho",
                "government hill", "admiralty", "mid-levels"],
    "Sheung Wan": ["sheung wan", "central and western", "central", "中西區", "上環"],
    "Admiralty": ["admiralty", "central and western", "central", "金鐘", "wan chai", "灣仔"],
    "Wan Chai": ["wan chai", "wanchai", "灣仔", "causeway bay", "happy valley"],
    "Causeway Bay": ["causeway bay", "wan chai", "wanchai", "銅鑼灣", "灣仔"],
    "Tin Hau": ["tin hau", "causeway bay", "wan chai", "eastern", "天后", "fortress hill",
                "tai hang", "north point", "東區"],
    "Happy Valley": ["happy valley", "wan chai", "跑馬地", "灣仔"],
    "Tsim Sha Tsui": ["tsim sha tsui", "yau tsim mong", "尖沙咀", "油尖旺", "kowloon",
                      "west kowloon", "jordan", "tsim sha tsui east"],
    "Jordan": ["jordan", "yau ma tei", "yau tsim mong", "佐敦", "油尖旺", "tsim sha tsui"],
    "Sai Kung": ["sai kung", "西貢"],
    # Macau
    "Cotai": ["cotai", "coloane", "taipa", "路氹", "氹仔", "路環", "cotai strip",
              "concelho das ilhas", "ilhas", "macau"],
    "Taipa": ["taipa", "氹仔", "cotai", "ilhas", "macau"],
    "Se": ["se", "sé", "macau", "澳門", "nape", "santo antonio", "são lázaro",
           "sao lazaro", "freguesia"],
}

# ---------------------------------------------------------------------------
# HTTP helpers with an on-disk cache so reruns are cheap and deterministic
# ---------------------------------------------------------------------------
_cache: dict = {}
_last_call = [0.0]


def load_cache() -> None:
    global _cache
    if CACHE.exists():
        _cache = json.loads(CACHE.read_text(encoding="utf-8"))


def save_cache() -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(_cache, ensure_ascii=False, indent=1), encoding="utf-8")


def _get(url: str) -> object:
    if url in _cache:
        return _cache[url]
    wait = RATE_LIMIT_S - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    _last_call[0] = time.time()
    _cache[url] = data
    save_cache()
    return data


def geocode(query: str, city: str):
    # NOTE: countrycodes=hk / countrycodes=mo returns *nothing* for these
    # territories -- Nominatim files them under CN -- so the filter is omitted
    # and the BBOX check below does the geographic guarding instead.
    url = (f"{NOMINATIM}/search?q={urllib.parse.quote(query)}"
           f"&format=jsonv2&limit=1&addressdetails=1")
    res = _get(url)
    return res[0] if res else None


def reverse(lat: float, lon: float):
    url = (f"{NOMINATIM}/reverse?lat={lat:.7f}&lon={lon:.7f}"
           f"&format=jsonv2&zoom=16&addressdetails=1")
    return _get(url)


# ---------------------------------------------------------------------------
# OSM corroboration
# ---------------------------------------------------------------------------
def norm(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    for a, b in [("’", "'"), ("‘", "'"), ("`", "'"),
                 ("–", "-"), ("—", "-")]:
        s = s.replace(a, b)
    s = re.sub(r"\bhong kong\b", "", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def fetch_osm_pois() -> dict[str, list[tuple[float, float]]]:
    """One bulk Overpass call; exact-name index of food POIs in HK + Macau."""
    key = "overpass:poi:v1"
    if key in _cache:
        elements = _cache[key]
    else:
        q = """
[out:json][timeout:180];
(
  nwr["amenity"~"^(restaurant|cafe|fast_food|bar|pub)$"](22.15,113.82,22.58,114.44);
  nwr["amenity"~"^(restaurant|cafe|fast_food|bar|pub)$"](22.10,113.52,22.22,113.60);
);
out center tags;
"""
        elements = []
        for ep in OVERPASS:
            try:
                req = urllib.request.Request(
                    ep, data=urllib.parse.urlencode({"data": q}).encode(),
                    headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=300) as r:
                    elements = json.loads(r.read().decode("utf-8"))["elements"]
                break
            except Exception as exc:  # pragma: no cover - network dependent
                print(f"  overpass {ep} failed: {exc!r}", file=sys.stderr)
        _cache[key] = elements
        save_cache()

    idx: dict[str, list[tuple[float, float]]] = {}
    for e in elements:
        t = e.get("tags", {})
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None:
            continue
        names = set()
        for k in ("name", "name:en", "alt_name", "alt_name:en", "official_name"):
            v = t.get(k)
            if v:
                names.add(norm(v))
                latin = re.sub(r"[^\x00-\x7F]+", " ", v).strip()
                if latin:
                    names.add(norm(latin))
        for n in names:
            if n:
                idx.setdefault(n, []).append((lat, lon))
    return idx


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ---------------------------------------------------------------------------
def in_bbox(lat: float, lon: float, city: str) -> bool:
    lo_lat, hi_lat, lo_lon, hi_lon = BBOX[city]
    return lo_lat <= lat <= hi_lat and lo_lon <= lon <= hi_lon


def district_ok(district: str, rev: dict) -> tuple[bool, str]:
    addr = rev.get("address", {}) or {}
    blob = " | ".join(str(v) for v in addr.values()) + " | " + rev.get("display_name", "")
    low = blob.lower()
    for token in DISTRICT_ALIASES.get(district, [district.lower()]):
        if token.lower() in low:
            return True, blob
    return False, blob


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write results into the seed file")
    args = ap.parse_args()

    load_cache()
    seed = json.loads(SEED.read_text(encoding="utf-8"))

    print("fetching OSM corroboration set ...", file=sys.stderr)
    osm_idx = fetch_osm_pois()

    by_key = {(v["name"], v["city"]): v for v in VENUES}
    rows = []

    for place in seed["places"]:
        key = (place["name"], place["city"])
        v = by_key.get(key)
        if not v:
            rows.append(dict(name=place["name"], city=place["city"], status="no-venue",
                             lat=None, lng=None, address=None, district=None,
                             source=None, confidence=None, rev="", note="no source address"))
            continue

        hit = geocode(v["query"], v["city"])
        if not hit:
            rows.append(dict(name=place["name"], city=place["city"], status="geocode-fail",
                             lat=None, lng=None, address=v["address"], district=v["district"],
                             source=v["source"], confidence=None, rev="",
                             note=f"nominatim empty for {v['query']!r}"))
            continue

        lat, lon = float(hit["lat"]), float(hit["lon"])
        conf = "verified" if v["kind"] == "address" else "hotel"
        note = ""
        # addresstype "road" means Nominatim could not resolve the house number
        # and fell back to a point on the street. On a long road (Lockhart Road
        # runs 1.5 km) that is off by hundreds of metres -- a wrong pin dressed
        # up as an address match. Only keep it if an OSM POI corroborates below.
        street_only = hit.get("addresstype") == "road"

        if not in_bbox(lat, lon, v["city"]):
            rows.append(dict(name=place["name"], city=place["city"], status="out-of-bbox",
                             lat=None, lng=None, address=v["address"], district=v["district"],
                             source=v["source"], confidence=None, rev="",
                             note=f"{lat:.5f},{lon:.5f} outside {v['city']} bbox"))
            continue

        # OSM corroboration: an exact-name POI near the geocoded venue is an
        # independent confirmation, and is a better pin than a building centroid.
        cands = osm_idx.get(norm(place["name"]), [])
        near = [c for c in cands if haversine_m((lat, lon), c) <= 500]
        if near:
            d = haversine_m((lat, lon), near[0])
            note = f"OSM POI agrees ({d:.0f} m); pin moved to POI"
            lat, lon = near[0]
            conf = "verified"
            street_only = False
        elif cands:
            d = min(haversine_m((lat, lon), c) for c in cands)
            note = f"OSM same-name POI rejected ({d/1000:.1f} km away)"

        if street_only:
            rows.append(dict(name=place["name"], city=place["city"], status="street-centroid",
                             lat=None, lng=None, address=v["address"], district=v["district"],
                             source=v["source"], confidence=None, rev="",
                             note="Nominatim fell back to a street centroid "
                                  "(no house number, no OSM POI); dropped"))
            continue

        rev = reverse(lat, lon)
        ok, blob = district_ok(v["district"], rev)
        if not ok:
            rows.append(dict(name=place["name"], city=place["city"], status="district-mismatch",
                             lat=None, lng=None, address=v["address"], district=v["district"],
                             source=v["source"], confidence=None, rev=blob,
                             note=f"expected {v['district']}; dropped"))
            continue

        rows.append(dict(name=place["name"], city=place["city"], status="ok",
                         lat=round(lat, 6), lng=round(lon, 6), address=v["address"],
                         district=v["district"], source=v["source"], confidence=conf,
                         rev=blob, note=note))

    # ---- report ----------------------------------------------------------
    w = max(len(r["name"]) for r in rows) + 1
    print(f"\n{'name':<{w}} {'cy':<3} {'lat':>10} {'lng':>11} {'conf':<9} rev-geocode / note")
    print("-" * 150)
    for r in rows:
        lat = f"{r['lat']:.6f}" if r["lat"] is not None else "-"
        lng = f"{r['lng']:.6f}" if r["lng"] is not None else "-"
        tail = r["rev"][:80] if r["status"] == "ok" else f"[{r['status']}] {r['note']}"
        if r["status"] == "ok" and r["note"]:
            tail += f"  << {r['note']}"
        print(f"{r['name']:<{w}} {r['city']:<3} {lat:>10} {lng:>11} "
              f"{str(r['confidence']):<9} {tail}")

    verified = sum(1 for r in rows if r["confidence"] == "verified")
    hotel = sum(1 for r in rows if r["confidence"] == "hotel")
    missing = sum(1 for r in rows if r["confidence"] is None)
    print(f"\nverified={verified}  hotel={hotel}  unresolved={missing}  total={len(rows)}")

    # duplicate-coordinate clustering
    clusters: dict[tuple, list[str]] = {}
    for r in rows:
        if r["lat"] is not None:
            clusters.setdefault((r["lat"], r["lng"]), []).append(r["name"])
    print("\nshared coordinates (same building = expected):")
    for pt, names in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
        if len(names) > 1:
            flag = "  <-- SUSPICIOUS (>5)" if len(names) > 5 else ""
            print(f"  {pt[0]:.6f},{pt[1]:.6f}  x{len(names)}: {', '.join(names)}{flag}")

    if args.apply:
        res = {(r["name"], r["city"]): r for r in rows}
        for place in seed["places"]:
            r = res[(place["name"], place["city"])]
            place["lat"] = r["lat"]
            place["lng"] = r["lng"]
            place["address"] = r["address"]
            place["district"] = r["district"]
            place["coord_source"] = r["source"] if r["lat"] is not None else None
            place["coord_confidence"] = r["confidence"]
        seed["counts"]["with_coordinates"] = verified + hotel
        SEED.write_text(json.dumps(seed, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
        print(f"\nwrote {SEED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
