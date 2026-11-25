/* HyperVision frontend preview data. All values in this file are illustrative. */
window.HyperVisionData = {
  performance: [92, 74, 81, 63, 69, 51, 61],
  performanceLabels: ['10:00', '10:05', '10:10', '10:15', '10:20', '10:25', 'Now'],
  cpuSeries: [34, 42, 39, 56, 48, 61, 42],
  memorySeries: [54, 57, 58, 60, 61, 63, 63],
  recentQueries: [
    { sql: 'SELECT department, AVG(salary)…', type: 'SELECT', latency: '61.2 ms', rows: '12', status: 'Complete' },
    { sql: 'SELECT * FROM orders LIMIT 50', type: 'SELECT', latency: '24.8 ms', rows: '50', status: 'Complete' },
    { sql: 'SELECT COUNT(*) FROM sales', type: 'AGGREGATE', latency: '38.4 ms', rows: '1', status: 'Complete' },
    { sql: 'SELECT region, SUM(salary)…', type: 'AGGREGATE', latency: '87.6 ms', rows: '6', status: 'Review' }
  ],
  queryPresets: {
    aggregate: 'SELECT department, AVG(salary)\nFROM sales\nWHERE salary > 50000\nGROUP BY department;',
    orders: 'SELECT order_id, employee_id, amount\nFROM orders\nORDER BY amount DESC LIMIT 10;',
    count: 'SELECT COUNT(*) AS total_sales\nFROM sales;',
    join: 'SELECT order_id, amount, department\nFROM orders JOIN sales ON orders.employee_id = sales.employee_id\nLIMIT 20;'
  },
  tables: {
    sales: {
      columns: ['employee_id', 'department', 'region', 'salary'],
      types: ['INT', 'VARCHAR', 'VARCHAR', 'DECIMAL'],
      rows: [
        [1001, 'Engineering', 'North', '78000.00'],
        [1002, 'Sales', 'West', '54000.00'],
        [1003, 'Engineering', 'East', '82000.00'],
        [1004, 'HR', 'South', '51000.00'],
        [1005, 'Finance', 'West', '69000.00']
      ],
      total: '100,000 rows'
    },
    orders: {
      columns: ['order_id', 'employee_id', 'amount'],
      types: ['INT', 'INT', 'DECIMAL'],
      rows: [
        [50001, 1001, '1250.00'],
        [50002, 1003, '890.50'],
        [50003, 1002, '2400.00'],
        [50004, 1005, '675.00'],
        [50005, 1004, '3100.00']
      ],
      total: '10,000 rows'
    }
  },
  indexes: [],
  findings: [
    { id: 'F-001', priority: 'medium', title: 'Aggregation query has elevated latency', description: 'The sample GROUP BY query takes 61.2 ms in the illustrative run. Inspect the query plan and compare repeated runs before choosing an optimization.', evidence: 'Sample latency: 61.2 ms', recommendation: 'Review grouping strategy and evaluate whether a suitable index can help the filter.' },
    { id: 'F-002', priority: 'medium', title: 'Salary filter may benefit from index evaluation', description: 'The query filters sales records with salary > 50000. An index may help depending on selectivity and the executor implementation.', evidence: 'Predicate: salary > 50000', recommendation: 'Benchmark the query with and without an appropriate salary index and verify result equivalence.' },
    { id: 'F-003', priority: 'info', title: 'Memory pressure appears stable in this sample', description: 'The displayed memory snapshot has no sustained upward trend. This is only an illustrative UI state, not a real process measurement.', evidence: 'Sample memory: 62.5 MB', recommendation: 'Connect real process monitoring and collect timestamped measurements before drawing conclusions.' }
  ],
  experiments: [
    { id: 'EXP-1031', query: 'Department salary average', config: 'threads=1 · pushdown=off', latency: '61.2 ms', check: 'Equivalent', status: 'Complete', state: 'complete' },
    { id: 'EXP-1030', query: 'Orders top 10', config: 'threads=1 · index=demo', latency: '24.8 ms', check: 'Equivalent', status: 'Complete', state: 'complete' },
    { id: 'EXP-1029', query: 'Sales by region', config: 'threads=2 · aggregation=on', latency: '87.6 ms', check: 'Not verified', status: 'Review', state: 'review' },
    { id: 'EXP-1028', query: 'Count sales records', config: 'threads=1 · default', latency: '38.4 ms', check: 'Equivalent', status: 'Complete', state: 'complete' }
  ]
};
