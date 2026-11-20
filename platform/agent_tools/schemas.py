INPUT_SCHEMA = {
 'type':'object','required':['input','state'],'properties':{'input':{'type':'object'},'state':{'type':'object'}}
}
TOOL_SCHEMAS = {
 'uav.get_profile': {'type':'object','required':['uavId','profile','evidence']},
 'uav.get_capability_baseline': {'type':'object','required':['uavId','capabilities','evidence']},
 'uav.query_flight_logs': {'type':'object','required':['records','recordCount','evidence']},
 'uav.get_mission_summary': {'type':'object','required':['missions','missionCount','evidence']},
 'uav.calculate_energy_consumption': {'type':'object','required':['energyUsedWh','sampleCount','evidence']},
 'uav.get_battery_health': {'type':'object','required':['soh','risk','evidence']},
 'uav.check_requirement_compliance': {'type':'object','required':['checks','compliant','evidence']},
 'uav.find_capability_gap': {'type':'object','required':['gaps','hasGap','evidence']},
}
