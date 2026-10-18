-- One row per order line. Carries the order's date and customer, so customer dimensions are
-- one entity hop away.
select
    i.order_item_id,
    i.order_id,
    o.order_date,
    o.customer_id,
    i.product_id,
    i.quantity,
    i.unit_price,
    i.discount,
    i.quantity * i.unit_price as gross_amount,
    i.quantity * i.unit_price * i.discount as discount_amount,
    i.quantity * i.unit_price * (1 - i.discount) as revenue,
    i.quantity * p.unit_cost as cost
from {{ source('raw', 'order_items') }} as i
join {{ source('raw', 'orders') }} as o using (order_id)
join {{ source('raw', 'products') }} as p using (product_id)
