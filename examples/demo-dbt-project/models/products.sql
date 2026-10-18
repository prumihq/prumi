select
    product_id,
    name,
    category,
    unit_cost
from {{ source('raw', 'products') }}
