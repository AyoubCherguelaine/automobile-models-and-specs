# Relational column reference

All engine fields are actual SQL columns. Source text is retained verbatim; derived numeric values are nullable. The `vehicle_specs` view adds brand country, model years, and all vehicle details to each engine row.

| Table | Column | Meaning |
| --- | --- | --- |
| brands | country | Marque country of origin; unknown is NULL |
| automobiles | year | Named model year or production start; URL year fallback |
| automobiles | year_end | End of named production range; NULL for Present/unknown |
| automobiles | year_source | `name`, `url`, or NULL |
| automobile_photos | automobile_id, position, url | One URL per ordered photo |
| engines | engine_type | I4, V6, V8, VR6, H4, R2, Electric, etc. |
| engines | cylinders | Cylinder count; no count inferred for rotary/electric engines |
| engines | rotors | Rotary rotor count |
| engines | gearbox_speeds | Explicit speed count from gearbox text |
| engines | power_rpm, torque_rpm | First explicit source RPM value |

## Source text columns

| Engine column | Source section | Source field |
| --- | --- | --- |
| brakes_front | Brakes Specs | Front: |
| brakes_rear | Brakes Specs | Rear: |
| drag_coefficient | Dimensions | Aerodynamics (Cd): |
| frontal_area | Dimensions | Aerodynamics (Frontal Area): |
| cargo_volume | Dimensions | Cargo Volume: |
| front_rear_track | Dimensions | Front/Rear Track: |
| ground_clearance | Dimensions | Ground Clearance: |
| height | Dimensions | Height: |
| length | Dimensions | Length: |
| turning_circle_curb_to_curb | Dimensions | Turning Circle (Curb To Curb): |
| turning_circle_wall_to_wall | Dimensions | Turning Circle (Wall To Wall): |
| turning_circle | Dimensions | Turning Circle: |
| wheelbase | Dimensions | Wheelbase: |
| width | Dimensions | Width: |
| cng_cylinder_capacity | Engine Specs | Cng Cylinder Capacity: |
| cylinders_raw | Engine Specs | Cylinders: |
| size | Engine Specs | Displacement: |
| engine_code | Engine Specs | Engine Code: (when supplied) |
| electrical_motor_power | Engine Specs | Electrical Motor Power: |
| electrical_motor_torque | Engine Specs | Electrical Motor Torque: |
| fuel_capacity_cng | Engine Specs | Fuel Capacity (Cng): |
| fuel_capacity_hydrogen | Engine Specs | Fuel Capacity (Hydrogen): |
| fuel_capacity_optional | Engine Specs | Fuel Capacity (Optional): |
| fuel_capacity | Engine Specs | Fuel Capacity: |
| fuel_system | Engine Specs | Fuel System: |
| fuel | Engine Specs | Fuel: |
| power | Engine Specs | Power: |
| torque | Engine Specs | Torque: |
| total_maximum_power | Engine Specs | Total Maximum Power: |
| total_maximum_torque | Engine Specs | Total Maximum Torque: |
| epa_city_epa | Fuel Economy (Epa) | City (Epa): |
| epa_combined_epa | Fuel Economy (Epa) | Combined (Epa): |
| epa_highway_epa | Fuel Economy (Epa) | Highway (Epa): |
| nedc_city_cng | Fuel Economy (Nedc) | City (Cng): |
| nedc_city | Fuel Economy (Nedc) | City: |
| nedc_co2_emissions | Fuel Economy (Nedc) | Co2 Emissions: |
| nedc_combined_cng | Fuel Economy (Nedc) | Combined (Cng): |
| nedc_combined | Fuel Economy (Nedc) | Combined: |
| nedc_highway_cng | Fuel Economy (Nedc) | Highway (Cng): |
| nedc_highway | Fuel Economy (Nedc) | Highway: |
| wltp_co2_emissions_combined | Fuel Economy (Wltp) | Co2 Emissions (Combined): |
| wltp_co2_emissions_extra_high | Fuel Economy (Wltp) | Co2 Emissions (Extra High): |
| wltp_co2_emissions_high | Fuel Economy (Wltp) | Co2 Emissions (High): |
| wltp_co2_emissions_low | Fuel Economy (Wltp) | Co2 Emissions (Low): |
| wltp_co2_emissions_medium | Fuel Economy (Wltp) | Co2 Emissions (Medium): |
| wltp_combined_cng | Fuel Economy (Wltp) | Combined (Cng): |
| wltp_combined | Fuel Economy (Wltp) | Combined: |
| wltp_extra_high_cng | Fuel Economy (Wltp) | Extra High (Cng): |
| wltp_extra_high | Fuel Economy (Wltp) | Extra High: |
| wltp_high_cng | Fuel Economy (Wltp) | High (Cng): |
| wltp_high | Fuel Economy (Wltp) | High: |
| wltp_low_cng | Fuel Economy (Wltp) | Low (Cng): |
| wltp_low | Fuel Economy (Wltp) | Low: |
| wltp_medium_cng | Fuel Economy (Wltp) | Medium (Cng): |
| wltp_medium | Fuel Economy (Wltp) | Medium: |
| wltp_observations | Fuel Economy (Wltp) | Observations: |
| acceleration_0_100 | Performance Specs | Acceleration 0-62 Mph (0-100 Kph): |
| top_speed_electrical | Performance Specs | Top Speed (Electrical): |
| top_speed | Performance Specs | Top Speed: |
| battery_charger_type | Power System Specs | Charger Type: |
| battery_charging_time_normal | Power System Specs | Charging Time (Normal): |
| battery_charging_time_quick | Power System Specs | Charging Time (Quick): |
| battery_maximum_capacity | Power System Specs | Maximum Capacity: |
| battery_nominal_capacity | Power System Specs | Nominal Capacity: |
| battery_observations | Power System Specs | Observations: |
| battery_power_pack | Power System Specs | Power Pack: |
| battery_range | Power System Specs | Range: |
| tire_size | Tires Specs | Tire Size: |
| T_drive_type | Transmission Specs | Drive Type: |
| T_gearbox | Transmission Specs | Gearbox: |
| gross_weight_limit | Weight Specs | Gross Weight Limit: |
| unladen_weight | Weight Specs | Unladen Weight: |

## Derived numeric columns

| Engine column | SQL type | Unit |
| --- | --- | --- |
| power_kw | REAL | kW |
| power_hp | REAL | source HP/PS |
| torque_nm | REAL | Nm |
| displacement_cc | REAL | cm³ |
| fuel_capacity_l | REAL | liters |
| top_speed_kph | REAL | km/h |
| acceleration_0_100_s | REAL | seconds |
| electric_motor_power_kw | REAL | kW |
| electric_motor_torque_nm | REAL | Nm |
| total_maximum_power_kw | REAL | kW |
| total_maximum_torque_nm | REAL | Nm |
| length_mm | REAL | mm |
| width_mm | REAL | mm |
| height_mm | REAL | mm |
| wheelbase_mm | REAL | mm |
| ground_clearance_mm | REAL | mm |
| cargo_volume_l | REAL | liters |
| drag_coefficient_value | REAL | dimensionless |
| frontal_area_m2 | REAL | m² |
| unladen_weight_kg | REAL | kg |
| gross_weight_limit_kg | REAL | kg |
| battery_nominal_capacity_kwh | REAL | kWh |
| battery_maximum_capacity_kwh | REAL | kWh |
| battery_range_km | REAL | km |
| nedc_city_l_100km | REAL | L/100 km |
| nedc_co2_emissions_g_km | REAL | g/km |
| nedc_combined_l_100km | REAL | L/100 km |
| nedc_highway_l_100km | REAL | L/100 km |
| epa_city_epa_l_100km | REAL | L/100 km |
| epa_combined_epa_l_100km | REAL | L/100 km |
| epa_highway_epa_l_100km | REAL | L/100 km |
| wltp_co2_emissions_combined_g_km | REAL | g/km |
| wltp_co2_emissions_extra_high_g_km | REAL | g/km |
| wltp_co2_emissions_high_g_km | REAL | g/km |
| wltp_co2_emissions_low_g_km | REAL | g/km |
| wltp_co2_emissions_medium_g_km | REAL | g/km |
| wltp_combined_l_100km | REAL | L/100 km |
| wltp_extra_high_l_100km | REAL | L/100 km |
| wltp_high_l_100km | REAL | L/100 km |
| wltp_low_l_100km | REAL | L/100 km |
| wltp_medium_l_100km | REAL | L/100 km |

`scrape_runs` stores brands/discovered/attempted/succeeded/skipped/failed/requests/retried as INTEGER columns. Raw HTML and capture metadata stay in filesystem artifacts, outside the relational schema.

## Vehicle information (`automobiles`)

| Columns | Type | Meaning |
| --- | --- | --- |
| vehicle_category | TEXT | Broad product group: car, truck, van, bus, motorcycle, unknown |
| vehicle_type | TEXT | Specific body/use type: suv, pickup, sedan, etc. |
| classification_source | TEXT | Evidence used to classify; insufficient_evidence when unknown |
| model_name, model_family | TEXT | Clean model identity; family from breadcrumb/schema |
| body_style, body_type | TEXT | Verbatim source style and normalized category |
| segment | TEXT | Source market segment |
| generation, trim | TEXT | Explicit generation and trim; otherwise NULL |
| vehicle_configuration | TEXT | General source configuration; not treated as trim |
| production_years_text | TEXT | Source list of production years |
| assembly_location | TEXT | Source assembly location, distinct from brand origin |
| infotainment, autonomous_driving | TEXT | Original source feature descriptions |
| doors, seats, autonomy_level | INTEGER | Explicit counts; autonomy level 0–5 |
| supports_apple_carplay, supports_android_auto | BOOLEAN | TRUE when named; NULL when unknown |
| main_image_url | TEXT | Structured-data vehicle image URL |
| body_style_source | TEXT | `page_table`, `structured_data`, `model_introduction`, or `title` |
| metadata_source | TEXT | `page_metadata` or `title_only` |

Body style and segment retain the source's classifications, which can be inaccurate. Door/seat counts are not guessed from them. General Schema.org [vehicleConfiguration](https://schema.org/vehicleConfiguration) is preserved separately from trim.

## Engine classifications (`engines`)

| Column | Type | Values / rule |
| --- | --- | --- |
| powertrain_type | VARCHAR | Combustion, Electric, Hybrid, Mild hybrid, Plug-in hybrid, Hydrogen fuel cell |
| fuel_type | VARCHAR | Gasoline, Diesel, Ethanol, CNG, LPG, Hydrogen, Electric |
| aspiration | VARCHAR | Turbocharged, Supercharged, Twincharged, Naturally aspirated; explicit source evidence required |
| injection_type | VARCHAR | Common rail, Direct, Multipoint, Direct and multipoint, Monopoint, Indirect, Electronic injection, Carburetor |
| transmission_type | VARCHAR | Manual, Automatic, Dual-clutch, CVT, Automated manual, Single-speed |
| drive_layout | VARCHAR | FWD, RWD, AWD, 4WD |
| electric_motor_count | INTEGER | Explicit motor count from engine name/layout |

Classification uses existing source fuel, fuel-system, gearbox and drive text. Unknown classifications stay NULL; original values remain available for inspection.

## NHTSA source catalog (`data/catalogs/nhtsa/vehicles.db`)

`vehicles` contains only scalar fields: `source`, `make_id`, `model_id`, `vehicle_type_id`, `make_name`, `model_name`, `vehicle_category`, `vehicle_type`, `source_vehicle_type`, `source_url`, `fetched_at`, and `detail_level`. Its primary key is `(source, make_id, model_id, vehicle_type_id)`; a name alone is not an identity. API types are motorcycle (1), truck (3), and bus (5). Truck includes light and heavy vehicles; the listing cannot establish a weight class.

`work` checkpoints each `(make_id, vehicle_type_id)` with category, make name, status, attempts, last error and completion timestamp. `runs` records ID, start/finish times, status and attempted/succeeded/failed counts. Raw JSON responses stay in filesystem captures; no JSON column is added to SQLite.

## Motorcycle technical database (`data/motorcycles/vehicles.db`)

`vehicles` contains model identity, brand, description, year bounds, vehicle metadata, shared raw/numeric engine fields and registered motorcycle specification columns. Each generation page provides one technical set. Raw source columns use `spec_<section>_<field>` (for example `spec_engine_specs_compression_ratio` and `spec_transmission_specs_clutch`). `spec_columns` records each column's section and field label; a conflicting label fails transactionally. These columns retain source values, including unknown or newly introduced fields.

`photos` stores `url_hash`, `position`, and `url`. `work` stores URL, discovery/detail kind, status, attempts and last error. Model/spec/photo data and completed detail checkpoint commit together. Known measurements populate normalized companion columns, with ambiguous/missing units remaining NULL.

## Official manufacturer matrices (`data/manufacturers/specifications.db`)

`models` contains source URL and identifier, make/model, category/type, page title, HTML SHA-256 and fetch timestamp. `specification_tables` identifies table position and heading per source URL. `specification_cells` stores `source_url`, `table_position`, `row_position`, `column_position`, `row_span`, `column_span`, `is_header`, and `value`. All columns are scalar. Joined tables retain matrix layout; a table cell is not an independently normalized vehicle/engine configuration. `work` stores URL, status, attempts and last error.

`specification_context` stores `source_url`, `table_position` and nullable `tab_label`, linked to the source table. Labels are read through `tabpanel`/`aria-labelledby` references, rather than inferred from nearby engine specifications. Cached context backfill requires the exact saved HTML hash and preserves the source data and fetch timestamp.

`powertrain_specs` is a read view for Scania's row-oriented option tables. It joins make/model/category/type, source heading, source tab, source URL, table position and row position with these TEXT source values: `engine_code`, `output`, `torque`, `fuel`, `emission_technology`, `engine_pto`, `propulsion_power`, `regeneration_power`, `gearbox`, `installed_battery_capacity`, `charging_power`, and `pto_options`. Units and explanatory rows remain in the original matrix; alternative values remain unmodified. `source_tab` preserves explicit labels such as Euro 6 or Electric, without inferring a normalized emission class. This view does not infer displacement, cylinder count, model year or compatible chassis configurations. It excludes unit rows and retains distinct options across tables.

`bus_variant_fields` exposes Mercedes-Benz and Setra bus column variants and their field/value evidence, including source section and matrix positions. Shared values with column spans apply to each explicitly named header they cover. Single-row field labels and single-column variant headers are supported; other layouts stay in the raw matrices.

`bus_variant_specs` joins each source URL/variant name across its tables, exposing dimension and capacity TEXT columns (`length_mm_text`, `width_mm_text`, `height_mm_text`, `height_with_ac_mm_text`, `gross_vehicle_weight_kg_text`, `seats_standard_text`, `passenger_capacity_max_text`, `passenger_capacity_min_text`), engine/transmission option columns (`engine`, `engine_2` through `engine_4`, corresponding output/displacement/torque columns), electric drive, battery and charging technology. `manufacturer_views.py` contains the reviewed label mapping. Values with multiple alternatives, footnote markers or locale-specific separators remain source text. No configuration combinations or numeric conversions are inferred. Conflicting distinct values leave the summary field NULL, with all evidence retained in `bus_variant_fields`.
