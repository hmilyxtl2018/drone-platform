-- Initial fixed steps for the UAV capability-analysis POC.
INSERT OR IGNORE INTO agent_step(step_id,agent_key,sequence_no,step_key,name,description,parallel_group,required,retry_limit) VALUES
 ('uav-cap-parse','uav-capability-analyst',1,'parse-requirement','解析任务需求','提取 UAV、任务和硬软约束',NULL,1,1),
 ('uav-cap-baseline','uav-capability-analyst',2,'query-baseline','查询能力基线','读取本体档案和能力基线',NULL,1,2),
 ('uav-cap-evidence','uav-capability-analyst',3,'collect-evidence','采集飞行与试验证据','并行读取飞行、能耗和健康数据','evidence',1,2),
 ('uav-cap-gap','uav-capability-analyst',4,'assess-gap','计算能力缺口','将需求与基线和实绩进行比较',NULL,1,2),
 ('uav-cap-validate','uav-capability-analyst',5,'validate-report','校验评估报告','检查证据、输出契约和收敛条件',NULL,1,1);
INSERT OR IGNORE INTO agent_step_tool(step_id,tool_key) VALUES
 ('uav-cap-baseline','uav.get_profile'),
 ('uav-cap-baseline','uav.get_capability_baseline'),
 ('uav-cap-evidence','uav.query_flight_logs'),
 ('uav-cap-evidence','uav.get_mission_summary'),
 ('uav-cap-evidence','uav.calculate_energy_consumption'),
 ('uav-cap-evidence','uav.get_battery_health'),
 ('uav-cap-gap','uav.check_requirement_compliance'),
 ('uav-cap-gap','uav.find_capability_gap');
INSERT OR IGNORE INTO agent_loop_rule(loop_id,agent_key,trigger_condition,return_step_key,max_iterations,no_progress_limit,stop_action) VALUES
 ('uav-cap-evidence-loop','uav-capability-analyst','关键能力结论缺少证据','collect-evidence',3,2,'requires-human-review');
