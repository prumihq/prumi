select
    snapshot_date,
    product_id,
    units_on_hand
from {{ source('raw', 'inventory_snapshots') }}
