"""Execute the shipped renderer: safe attribution, complete legs, stale proposals and storage."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def test_review_renderer_navigation_and_storage():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node unavailable")
    root = Path(__file__).resolve().parents[2]
    module = (root / "src/harbor_lantern/web/js/render/reviewplan.js").as_uri()
    script = "import {reviewHtml, routeHtml, withoutReviews, proposalHtml} from " + json.dumps(module) + ";\n" + """
const malicious={status:'matched',rating:4.5,review_count:100,source_url:'javascript:alert(1)',
  reviews:[{author:'<img onerror=alert(1)>',text:'<script>bad()</script>',author_url:'javascript:bad()'}]};
const html=reviewHtml(malicious);
const route=routeHtml({name:'A',lat:22.3,lng:114.1},{name:'B',lat:22.4,lng:114.2});
const plan={review_summary:{counts:{matched:1}},days:[{stops:[{place:{name:'A',review:malicious}}]}]};
const saved=withoutReviews(plan);
const day={day_index:1,proposed_order:['a'],improved:true,advice:[],
  current:{day:{spots:[],totals:{travel_minutes:10,distance_m:500}}},
  proposed:{day:{spots:[],totals:{travel_minutes:5,distance_m:200}},warnings:[],conflicts:[]}};
const stale=proposalHtml({expected_revision:1,days:[day],review_summary:{counts:{}},reviews_enabled:true},2);
console.log(JSON.stringify({html,route,saved,originalHasReview:!!plan.days[0].stops[0].place.review,stale}));
"""
    run = subprocess.run([node, "--input-type=module"], input=script, capture_output=True, text=True, timeout=15)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert "<script>" not in result["html"] and "<img" not in result["html"]
    assert "javascript:" not in result["html"]
    assert "&lt;script&gt;" in result["html"]
    assert "origin=22.3%2C114.1" in result["route"]
    assert "destination=22.4%2C114.2" in result["route"]
    assert "travelmode=transit" in result["route"]
    assert "review_summary" not in result["saved"]
    assert "review" not in result["saved"]["days"][0]["stops"][0]["place"]
    assert result["originalHasReview"]
    assert 'data-apply-day="1" disabled' in result["stale"]
