"""
optimizer.py
------------
A very small "query optimizer". Real optimizers choose between many
possible execution strategies; ours applies one well-known rule called
predicate pushdown: run WHERE conditions as early as possible so later
steps (JOIN, GROUP BY) work on fewer rows.

Pushdown is OFF by default (settings.pushdown_filter). When it is on,
every WHERE condition that touches only ONE of the two joined tables is
applied to that table before the join; conditions that cannot be pushed
(columns that exist in both tables, or in neither) still run after it.

This file also builds the ordered list of operator names that is shown
as the "query plan".
"""


def split_where_conditions(conditions, left_columns, right_columns):
    """
    Splits WHERE conditions for a JOIN into
        (left-only conditions, right-only conditions, conditions that must stay after the join).
    """
    left_columns = set(left_columns or [])
    right_columns = set(right_columns or [])
    left_only, right_only, after_join = [], [], []
    for condition in conditions:
        column = condition[0]
        in_left = column in left_columns
        in_right = column in right_columns
        if in_left and not in_right:
            left_only.append(condition)
        elif in_right and not in_left:
            right_only.append(condition)
        else:
            after_join.append(condition)
    return left_only, right_only, after_join


def build_plan(query, settings=None, storage=None):
    """
    Ordered list of operator names for `query`. Pass `settings` and `storage`
    to get the plan that matches an execution with pushdown_filter turned on.
    """
    plan_steps = ["SCAN " + query.table]

    if query.join:
        plan_steps.append("SCAN " + query.join["table"])

        pushdown = (
            settings is not None and settings.pushdown_filter
            and storage is not None and query.where
        )
        if pushdown:
            left_table = storage.get_table(query.table)
            right_table = storage.get_table(query.join["table"])
            left_c, right_c, after_c = split_where_conditions(
                query.where, left_table.columns, right_table.columns)
            if left_c:
                plan_steps.append("FILTER " + query.table)
            if right_c:
                plan_steps.append("FILTER " + query.join["table"])
            plan_steps.append("HASH_JOIN")
            if after_c:
                plan_steps.append("FILTER")
        else:
            plan_steps.append("HASH_JOIN")
            if query.where:
                plan_steps.append("FILTER")
    elif query.where:
        plan_steps.append("FILTER")   # runs right after the scan

    has_aggregate = any(c["agg"] for c in query.columns)
    if query.group_by:
        plan_steps.append("HASH_GROUP_BY")
    elif has_aggregate:
        plan_steps.append("AGGREGATE")
    else:
        plan_steps.append("PROJECTION")

    if query.order_by:
        plan_steps.append("SORT")

    return plan_steps
