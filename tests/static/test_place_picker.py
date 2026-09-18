"""Provider identity must merge evidence without inventing ratings or mutating source data."""

from tests.static.test_plan_map import PROJECT_ROOT, _run_node, node_only


@node_only
def test_viewport_filters_missing_ratings_and_dateline():
    module = (PROJECT_ROOT / 'src/harbor_lantern/web/js/place-picker.js').as_uri()
    result = _run_node(f"""
        import {{filterPlaces,withinBounds}} from {module!r};
        const box={{south:-1,west:179,north:1,east:-179}},origin={{lat:0,lng:180}};
        const places=[
          {{id:'a',name:'Museum',category:'museum',lat:0,lng:179.9,review:{{source:'Trip.com',rating:4.8,review_count:900}}}},
          {{id:'b',name:'Cafe',category:'cafe',lat:0,lng:-179.9,review:{{source:'Trip.com',rating:4.2,review_count:2000}}}},
          {{id:'c',name:'Unknown',category:'park',lat:0,lng:179.8}},
          {{id:'d',name:'Outside',category:'museum',lat:0,lng:0}}
        ];
        const ids=f=>filterPlaces(places,f,origin,box).map(p=>p.id);
        console.log(JSON.stringify({{all:ids({{}}),rated:ids({{minRating:4.5}}),
          reviews:ids({{minReviews:1000}}),kind:ids({{category:'nature'}}),query:ids({{query:'museum'}}),
          sorted:ids({{sort:'reviews'}}),outside:withinBounds(places[3],box),
          untouched:places.every(p=>p.distance_m===undefined)}}));
    """)
    assert set(result['all']) == {'a', 'b', 'c'}
    assert result['rated'] == ['a'] and result['reviews'] == ['b']
    assert result['kind'] == ['c'] and result['query'] == ['a']
    assert result['sorted'] == ['b', 'a', 'c']
    assert not result['outside'] and result['untouched']


@node_only
def test_map_rating_labels_distinguish_missing_data():
    module = (PROJECT_ROOT / 'src/harbor_lantern/web/js/map.js').as_uri()
    result = _run_node(f"""
        import {{ratingLabel}} from {module!r};
        console.log(JSON.stringify([
          ratingLabel({{review:{{rating:4.6,review_count:1039}}}}),
          ratingLabel({{}}),ratingLabel({{review:{{rating:4.6}}}}),
          ratingLabel({{review:{{rating:9,review_count:3}}}})
        ]));
    """)
    assert result == ['★ 4.6 · 1,039개 평가', '평가 정보 없음', '평가 정보 없음', '평가 정보 없음']


@node_only
def test_picker_merges_verified_identity_preserves_sources_and_avoids_false_ratings():
    module = (PROJECT_ROOT / 'src/harbor_lantern/web/js/place-picker.js').as_uri()
    result = _run_node(f"""
        import {{mergePlaces,samePlace}} from {module!r};
        const source='https://www.trip.com/example?from=chatgpt&allianceid=123&sid=456';
        const guide={{id:'wd:Q1',name:'정원',name_original:'Garden',lat:50,lng:14,
          description:'Original text',description_source:{{url:'wiki',license:'CC BY-SA'}},
          review:{{source:'Trip.com',rating:4.7,review_count:300,source_url:source}}}};
        const live={{id:'way/1',wikidata_id:'Q1',name:'Garden',lat:50,lng:14,
          source:'OpenStreetMap',source_url:'https://www.openstreetmap.org/way/1'}};
        const original=JSON.stringify(guide);
        const merged=mergePlaces([guide],[live],'Garden');
        const unrelated={{...live,id:'way/2',wikidata_id:'Q2',lat:51}};
        console.log(JSON.stringify({{
          merged:merged.length,id:merged[0].id,rating:merged[0].review.rating,
          sourceUnchanged:merged[0].review.source_url===source,
          mapSource:merged[0].source_url,description:merged[0].description,
          noMutation:original===JSON.stringify(guide),duplicate:samePlace(guide,live),
          farAwayDuplicate:samePlace(live,unrelated),
          unrelatedGetsNoRating:!mergePlaces([guide],[unrelated],'missing')[0].review,
          empty:mergePlaces([guide],[],'missing').length
        }}));
    """)
    assert result == {
        'merged': 1, 'id': 'wd:Q1', 'rating': 4.7, 'sourceUnchanged': True,
        'mapSource': 'https://www.openstreetmap.org/way/1', 'description': 'Original text',
        'noMutation': True, 'duplicate': True, 'farAwayDuplicate': False,
        'unrelatedGetsNoRating': True, 'empty': 0,
    }
