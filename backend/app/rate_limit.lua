-- Each client IP has independent keys, shared across API replicas only.
-- There is no platform-wide or account request counter. Redis supplies the clock.
local clock = redis.call('TIME')
local stamp = tonumber(ARGV[1])
if stamp < 0 then stamp = tonumber(clock[1])*1000 + math.floor(tonumber(clock[2])/1000) end
local short_ms, short_limit = tonumber(ARGV[2]), tonumber(ARGV[3])
local long_ms, long_limit = tonumber(ARGV[4]), tonumber(ARGV[5])
local allowed, retry, triggers = 1, 0, {}
for i=1,#KEYS,2 do
    local attempts, cooldown = KEYS[i], KEYS[i+1]
    redis.call('ZREMRANGEBYSCORE', attempts, '-inf', stamp-long_ms)
    redis.call('ZADD', attempts, stamp, ARGV[6])
    -- Retaining limit+1 latest attempts is sufficient even during a flood.
    redis.call('ZREMRANGEBYRANK', attempts, 0, -(long_limit+2))
    redis.call('PEXPIRE', attempts, long_ms+1000)
    local total = redis.call('ZCARD', attempts)
    local short = redis.call('ZCOUNT', attempts, '('..(stamp-short_ms), '+inf')
    if short > short_limit then
        allowed = 0
        local oldest = redis.call('ZREVRANGE', attempts, short_limit-1, short_limit-1, 'WITHSCORES')
        retry = math.max(retry, tonumber(oldest[2])+short_ms-stamp)
    end
    if total > long_limit then
        allowed = 0
        local oldest = redis.call('ZREVRANGE', attempts, long_limit-1, long_limit-1, 'WITHSCORES')
        retry = math.max(retry, tonumber(oldest[2])+long_ms-stamp)
        if redis.call('SET', cooldown, '1', 'NX', 'PX', long_ms) then
            table.insert(triggers, (i+1)/2)
        end
    end
end
return {allowed, math.max(0, math.ceil(retry/1000)), stamp, triggers}
