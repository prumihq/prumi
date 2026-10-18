select
    order_id,
    order_date,
    customer_id,
    channel
from {{ source('raw', 'orders') }}
