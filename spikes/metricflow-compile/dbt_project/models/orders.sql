select
    order_id,
    order_date,
    customer_id,
    region,
    amount
from {{ ref('raw_orders') }}
