# Travel platform expansion

Status: active. Base: 889e72e. Preserve existing shared Hong Kong trips and database.

Acceptance: shared light design tokens/navigation; city catalogue for all 29 baked cities; city detail and dated itinerary with map/day tabs/edit/checklist/save; standalone nearby food search with location/city selection and map; evidence-based rating/count ranking with provider, source and retrieval date; no fabricated ratings or MCP runtime dependency.

Implementation: reuse FastAPI, existing guide planner, Leaflet and ES modules. Trip.com MCP is research-time input; match only city-scoped exact names or individually reviewed mappings. Missing evidence remains missing. Persist only aggregate rating/count/source, not review bodies. Preserve provider URLs. Google live lookup stays explicitly opt-in.

Design: redesign-existing-projects; product UI in native CSS. Light mineral background, forest-green accent, editorial city photography, compact top navigation, split map/timeline. Variance 6, motion 3, density 5. Existing audit: hidden catalogue, disconnected dark Hong Kong screen, overlong one-page flow, no itinerary editing outside Hong Kong.

Validation: 906 pytest passed; Ruff, vendor 7 files, document contracts passed. Chromium desktop/mobile verified real OSM tiles, catalogue, rating evidence, duration edit, remove/add, reorder, move, checklist, saved reload, food fixture/map and shared Hong Kong creation. Zero JS errors and no mobile overflow. Documentation: ../_change_platform.md. Deployment follows checks.
