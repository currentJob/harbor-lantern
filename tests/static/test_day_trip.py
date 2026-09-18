"""The excursion replaces one local day reversibly and keeps the other days intact."""
from tests.static.test_plan_map import PROJECT_ROOT, _run_node, node_only


@node_only
def test_macau_apply_restore_and_duplicate_guard():
    module = (PROJECT_ROOT / 'src/harbor_lantern/web/js/day-trip.js').as_uri()
    result = _run_node(f"""
      import {{readFileSync}} from 'node:fs';
      import {{withMacauDay,restoreMacauDay,canAddMacau,excursionHtml}} from {module!r};
      const template=JSON.parse(readFileSync('src/harbor_lantern/web/data/macau-day-trip.json','utf8'));
      const plan={{local_id:'mine',start_date:'2026-10-05',end_date:'2026-10-08',
        destination:{{name:'홍콩'}},guide_city:{{city_id:'hong-kong'}},
        days:[5,6,7,8].map(d=>({{date:'2026-10-0'+d,custom:'keep-'+d,
          stops:[{{place:{{id:'p'+d,name:'original'}},completed:true,fixed_start:'12:15'}}]}}))}};
      const before=JSON.stringify(plan);
      const next=withMacauDay(plan,template,'2026-10-07');
      const restored=restoreMacauDay(next);
      let duplicate=false,invalid=false;
      try{{withMacauDay(next,template,'2026-10-06')}}catch{{duplicate=true}}
      try{{withMacauDay(plan,template,'2026-10-09')}}catch{{invalid=true}}
      const otherDate=withMacauDay(plan,template,'2026-10-06');
      const malicious=structuredClone(next.days[2]);malicious.excursion.title='<script>x</script>';
      console.log(JSON.stringify({{
        unchanged:before===JSON.stringify(plan),id:next.local_id,
        otherDays:[0,1,3].every(i=>JSON.stringify(next.days[i])===JSON.stringify(plan.days[i])),
        restored:JSON.stringify(restored.days)===JSON.stringify(plan.days),
        duplicate,invalid,count:next.scheduled_count,weekday:otherDate.days[1].weekday,
        date:otherDate.days[1].date,city:next.days[2].guide_city.city_id,
        terminalStart:next.days[2].stops[0].place.name,terminalEnd:next.days[2].stops.at(-1).place.name,
        sources:excursionHtml(next.days[2]).includes('gov.mo'),
        escaped:!excursionHtml(malicious).includes('<script>'),
        wrongTrip:canAddMacau({{...plan,destination:{{name:'도쿄'}},guide_city:{{city_id:'tokyo'}}}})
      }}));
    """)
    assert result['unchanged'] and result['otherDays'] and result['restored']
    assert result['id'] == 'mine'
    assert result['duplicate'] and result['invalid'] and not result['wrongTrip']
    assert result['count'] == 9
    assert result['date'] == '2026-10-06' and result['weekday'] == 1
    assert result['city'] == 'macau'
    assert '외항' in result['terminalStart'] and '외항' in result['terminalEnd']
    assert result['sources'] and result['escaped']
