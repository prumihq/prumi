select
    customer_id,
    signup_date,
    country,
    region,
    segment
from {{ source('raw', 'customers') }}
