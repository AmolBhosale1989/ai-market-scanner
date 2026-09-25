-- Recovery ownership is separate from immutable publication history.
CREATE TABLE pipeline_lease (
    scope TEXT PRIMARY KEY,
    owner_id UUID NOT NULL,
    generation BIGINT NOT NULL CHECK (generation > 0),
    market_session DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
    acquired_at TIMESTAMPTZ NOT NULL,
    heartbeat_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE pipeline_lease_history (
    scope TEXT NOT NULL,
    generation BIGINT NOT NULL,
    owner_id UUID NOT NULL,
    market_session DATE NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    outcome TEXT NOT NULL CHECK (outcome IN ('completed','failed','abandoned')),
    PRIMARY KEY (scope,generation)
);
