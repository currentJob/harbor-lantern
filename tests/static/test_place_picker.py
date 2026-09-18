"""Provider identity must merge evidence without inventing ratings or mutating source data."""

from tests.static.test_plan_map import PROJECT_ROOT, _run_node, node_only


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
