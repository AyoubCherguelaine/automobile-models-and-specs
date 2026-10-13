"""Queryable source values for reviewed manufacturer specification table orientations."""

BUS_FIELDS = {
    "length_mm_text": ("Length [mm]",),
    "width_mm_text": ("Width [mm]",),
    "height_mm_text": ("Height [mm]", "Elevation [mm]"),
    "height_with_ac_mm_text": ("Height incl. air-conditioning system [mm]",),
    "gross_vehicle_weight_kg_text": ("Gross vehicle weight [kg]",),
    "seats_standard_text": ("Number of seats – standard seating",),
    "passenger_capacity_max_text": ("Passenger capacity max.",),
    "passenger_capacity_min_text": ("Passenger capacity min.",),
    "engine_type": ("Engine type",),
    "emissions_standard": ("EU emissions standard",),
    "electric_drive": ("Electric drive",),
    "power_peak_continuous_kw_text": ("Power (peak/ duration) [kW]",),
    "battery_modules_text": ("Number of high-voltage NMC battery modules",),
    "battery_module_capacity_kwh_text": ("Capacity per NMC battery modules [kWh]",),
    "battery_capacity_options_text": ("Capacity equipment variant NMC battery [6/8/10/12/]",),
    "fuel_tank_capacity_l_text": ("Capacity of fuel tank [l]",),
    "fuel_tanks_left_right_l_text": ("Capacity fuel tank left / right [l]",),
    "torque_nm_text": ("Max. torque [Nm]",),
    "front_overhang_mm_text": ("Overhang at front [mm]",),
    "rear_overhang_mm_text": ("Overhang at rear [mm]",),
}
for option in range(1, 5):
    source_suffix = "" if option == 1 else f" {option}*"
    column_suffix = "" if option == 1 else f"_{option}"
    BUS_FIELDS[f"engine{column_suffix}"] = (f"Engine{source_suffix}",)
    BUS_FIELDS[f"output{column_suffix}_kw_text"] = (f"Output{source_suffix} [kW]",)
    BUS_FIELDS[f"displacement{column_suffix}_l_text"] = (
        f"Displacement{source_suffix} [l]",
        f"Displacement{source_suffix} [l-litre]",
    )
    BUS_FIELDS[f"transmission{column_suffix}"] = (f"Transmission{source_suffix}",)
    if option > 1:
        BUS_FIELDS[f"torque{column_suffix}_nm_text"] = (f"Max. torque{source_suffix} [Nm]",)
    if option <= 3:
        BUS_FIELDS[f"charging_technology{column_suffix}"] = (f"Charging technology{source_suffix}",)


def create_bus_views(connection):
    # Owned views are replaced in the store's transaction so new reviewed sources
    # and label mappings also apply to databases created by earlier versions.
    connection.execute("DROP VIEW IF EXISTS bus_variant_specs")
    connection.execute("DROP VIEW IF EXISTS bus_variant_fields")
    connection.execute("""
        CREATE VIEW IF NOT EXISTS bus_variant_fields AS
        SELECT m.source,m.make_name,m.model_name,m.vehicle_category,m.vehicle_type,
            h.value AS variant_name,c.source_url,c.table_position,
            c.row_position,c.column_position,h.column_position AS variant_column_position,
            t.heading AS source_heading,l.value AS field_name,c.value,
            (SELECT s.value FROM specification_cells s
                WHERE s.source_url=c.source_url AND s.table_position=c.table_position
                    AND s.column_position=0 AND s.column_span>1
                    AND s.row_position<c.row_position
                ORDER BY s.row_position DESC LIMIT 1) AS source_section
        FROM specification_cells c
        JOIN specification_cells h
            ON h.source_url=c.source_url AND h.table_position=c.table_position
                AND h.row_position=0 AND h.is_header=1
                AND h.column_position>=c.column_position
                AND h.column_position<c.column_position+c.column_span
                AND h.column_position>0 AND h.row_span=1 AND h.column_span=1
                AND trim(h.value)!=''
        JOIN specification_cells l
            ON l.source_url=c.source_url AND l.table_position=c.table_position
                AND l.row_position=c.row_position AND l.column_position=0
                AND l.row_span=1 AND l.column_span=1 AND trim(l.value)!=''
        JOIN specification_tables t USING(source_url,table_position)
        JOIN models m ON m.source_url=c.source_url
        WHERE m.source IN ('mercedes_benz','setra') AND m.vehicle_category='bus'
            AND c.column_position>0 AND c.row_position>0 AND c.row_span=1
    """)
    columns = []
    for column, labels in BUS_FIELDS.items():
        # Labels and SQL identifiers come only from the fixed, reviewed mapping above.
        quoted = ",".join("'" + label.casefold().replace("'", "''") + "'" for label in labels)
        value = (
            f"CASE WHEN lower(trim(replace(field_name,char(160),' '))) IN ({quoted}) THEN value END"
        )
        columns.append(f"CASE WHEN count(DISTINCT {value})=1 THEN max({value}) END AS {column}")
    connection.execute(f"""
        CREATE VIEW IF NOT EXISTS bus_variant_specs AS
        SELECT source,make_name,model_name,vehicle_category,vehicle_type,variant_name,source_url,
            {",".join(columns)}
        FROM bus_variant_fields
        GROUP BY source,make_name,model_name,vehicle_category,vehicle_type,variant_name,source_url
    """)


def create_powertrain_view(connection):
    # Scania publishes engine/powertrain options as rows. Bus column-oriented
    # specifications are handled by the separate views above.
    existing = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name='powertrain_specs'"
    ).fetchone()
    if existing and "source_tab" not in {
        row[1] for row in connection.execute("PRAGMA table_info(powertrain_specs)")
    }:
        connection.execute("DROP VIEW powertrain_specs")
    connection.execute("""
        CREATE VIEW IF NOT EXISTS powertrain_specs AS
        WITH option_rows AS (
            SELECT c.source_url,c.table_position,c.row_position,
                max(CASE h.value WHEN 'Engine ID' THEN c.value END) AS engine_code,
                max(CASE h.value WHEN 'Output' THEN c.value END) AS output,
                max(CASE h.value WHEN 'Torque' THEN c.value END) AS torque,
                max(CASE h.value WHEN 'Fuel' THEN c.value END) AS fuel,
                max(CASE h.value WHEN 'Emission technology' THEN c.value END)
                    AS emission_technology,
                max(CASE h.value WHEN 'Engine PTO' THEN c.value END) AS engine_pto,
                max(CASE h.value WHEN 'Propulsion Power' THEN c.value END)
                    AS propulsion_power,
                max(CASE WHEN h.value IN ('Regeration Power','Regeneration Power')
                    THEN c.value END) AS regeneration_power,
                max(CASE h.value WHEN 'Gearbox' THEN c.value END) AS gearbox,
                max(CASE h.value WHEN 'Installed battery capacity' THEN c.value END)
                    AS installed_battery_capacity,
                max(CASE h.value WHEN 'Charging power' THEN c.value END) AS charging_power,
                max(CASE h.value WHEN 'PTO options' THEN c.value END) AS pto_options
            FROM specification_cells c
            JOIN specification_cells h
                ON h.source_url=c.source_url AND h.table_position=c.table_position
                AND h.column_position=c.column_position AND h.row_position=0
                AND h.row_span=1 AND h.column_span=1
            JOIN models m ON m.source_url=c.source_url
            WHERE m.source='scania' AND c.row_position>0
                AND c.row_span=1 AND c.column_span=1
            GROUP BY c.source_url,c.table_position,c.row_position
        )
        SELECT m.source,m.make_name,m.model_name,m.vehicle_category,m.vehicle_type,
            t.heading AS source_heading,ctx.tab_label AS source_tab,o.*
        FROM option_rows o
        JOIN models m USING(source_url)
        JOIN specification_tables t USING(source_url,table_position)
        LEFT JOIN specification_context ctx USING(source_url,table_position)
        WHERE trim(coalesce(o.output,o.propulsion_power,'')) GLOB '[0-9]*'
    """)
