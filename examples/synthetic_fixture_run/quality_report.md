# PeeringDB IX nodes – quality report (run `00000000-0000-4000-8000-000000000149`)

* Scope: `{"base_url": "https://www.peeringdb.com/api", "countries": ["DE"], "region_continent": null, "requested_at": "2026-10-28T00:00:00+00:00", "synthetic": true}`
* Snapshot digest: `16584c11996224c14f64e72c3b28a4ba8cbe5eccb766fb00abf6644bd9d78405`
* Output digest: `5fd004a1284013825618d0ebe6551264c9d1c79f9839a3278d34b0e57967c6b3`

## Coverage

| metric | value |
|---|---|
| ix_total | 8 |
| ix_with_located_node | 7 |
| nodes_total | 11 |
| nodes_located | 10 |
| nodes_unlocated | 1 |
| location_coverage_pct | 90.9 |
| distinct_coordinates | 10 |
| largest_shared_coordinate | lat=50.1188, lon=8.7356, nodes=1 |

## Location source

| value | count |
|---|---|
| peeringdb_facility | 3 |
| geocoded_address | 2 |
| geocoded_city | 5 |
| none | 1 |

## Location accuracy

| value | count |
|---|---|
| facility_reported | 3 |
| building | 2 |
| city | 5 |
| unknown | 1 |

## Node type

| value | count |
|---|---|
| ix_facility | 9 |
| ix_city | 2 |

## Quality flags

| value | count |
|---|---|
| coordinates_null_island | 1 |
| coordinates_partial | 1 |
| cross_border_facility | 1 |
| facility_record_missing | 1 |
| geocode_miss:address | 1 |
| geocode_offline_miss:city | 1 |
| geocoded | 7 |
| lat_lon_swap_suspected | 1 |
| no_facility_link | 2 |
| outside_country_bbox | 2 |
| source_coordinates_missing | 1 |
| source_coordinates_rejected | 4 |
| unlocated | 1 |

## Rejected source records

| value | count |
|---|---|
| ix:duplicate_identical | 1 |
| ix:duplicate_superseded | 1 |
| ix:invalid_country_code | 1 |
| ix:missing_or_invalid_id | 1 |
| ix:missing_required:name | 1 |
| ix:not_an_object | 1 |
| ix:status_pending | 1 |
| ixfac:duplicate_ix_fac_pair | 1 |
| ixfac:missing_required:ix_id | 1 |
| ixfac:orphan_unknown_ix | 1 |

## Per country

| country | nodes | located |
|---|---|---|
| DE | 10 | 9 |
| NL | 1 | 1 |

## Geocoding

| outcome | count |
|---|---|
| cache_hit | 8 |
| network | 0 |
| miss | 1 |
| offline_miss | 1 |
| error | 0 |

## Staging load

| outcome | count |
|---|---|
| inserted | 11 |
| updated_changed | 0 |
| unchanged | 0 |
| reactivated | 0 |
| deactivated | 0 |
| rejects_stored | 10 |

## Acceptance gates

| gate | result | detail |
|---|---|---|
| not_collapsed_to_single_coordinate | PASS | 10 distinct coordinates for 10 located nodes; largest shared point holds 1 (10.0%), limit 50% |
| every_location_has_provenance | PASS | coord_source + accuracy + query/provider |
| unique_node_ids | PASS | 11 nodes |
| coordinates_inside_country_reference | PASS | 0 violations |
| fetch_complete | PASS | all API responses had a data array |

**All gates pass: True**
