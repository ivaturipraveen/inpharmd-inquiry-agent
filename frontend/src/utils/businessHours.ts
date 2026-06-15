// Lightweight client-side "is the manufacturer open right now?" check.
// Mirrors the canonical patterns handled by backend `call_service.parse_hours`
// so the UI can warn before dispatch — backend remains the source of truth.
//
// Returns true/false when we can determine, or null when we can't parse the
// string (caller should treat null as "let the server decide").

const TZ_MAP: Record<string, string> = {
  ET: "America/New_York",
  EDT: "America/New_York",
  EST: "America/New_York",
  EASTERN: "America/New_York",
  CT: "America/Chicago",
  CDT: "America/Chicago",
  CST: "America/Chicago",
  CENTRAL: "America/Chicago",
  MT: "America/Denver",
  MDT: "America/Denver",
  MST: "America/Denver",
  MOUNTAIN: "America/Denver",
  PT: "America/Los_Angeles",
  PDT: "America/Los_Angeles",
  PST: "America/Los_Angeles",
  PACIFIC: "America/Los_Angeles",
  UTC: "UTC",
  GMT: "UTC",
};

const WEEKDAY_INDEX: Record<string, number> = {
  mon: 1, monday: 1,
  tue: 2, tues: 2, tuesday: 2,
  wed: 3, weds: 3, wednesday: 3,
  thu: 4, thur: 4, thurs: 4, thursday: 4,
  fri: 5, friday: 5,
  sat: 6, saturday: 6,
  sun: 0, sunday: 0,
};

// "8a" → 8, "5p" → 17, "9am" → 9, "5:30pm" → 17.5
function parseTimeToken(raw: string): number | null {
  const t = raw.trim().toLowerCase();
  const m = /^(\d{1,2})(?::(\d{2}))?\s*([ap]m?)?$/.exec(t);
  if (!m) return null;
  let hour = Number(m[1]);
  const minutes = m[2] ? Number(m[2]) : 0;
  const suffix = m[3] ?? "";
  if (suffix.startsWith("p") && hour < 12) hour += 12;
  if (suffix.startsWith("a") && hour === 12) hour = 0;
  return hour + minutes / 60;
}

function parseWeekdays(raw: string): Set<number> | null {
  const t = raw.trim().toLowerCase();
  const range = /^([a-z]+)\s*[-–]\s*([a-z]+)$/.exec(t);
  if (range) {
    const a = WEEKDAY_INDEX[range[1]];
    const b = WEEKDAY_INDEX[range[2]];
    if (a == null || b == null) return null;
    const out = new Set<number>();
    // Walk forward (handles Sun-Thu etc.)
    let cur = a;
    for (let i = 0; i < 7; i++) {
      out.add(cur);
      if (cur === b) return out;
      cur = (cur + 1) % 7;
    }
    return out;
  }
  const single = WEEKDAY_INDEX[t];
  if (single != null) return new Set([single]);
  return null;
}

interface NowInZone {
  weekday: number; // 0-6, Sun-Sat
  hour: number; // 0-23.99
}

function nowInZone(tz: string): NowInZone | null {
  try {
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      weekday: "short",
      hour: "numeric",
      minute: "numeric",
      hour12: false,
    });
    const parts = fmt.formatToParts(new Date());
    const wdStr = parts.find((p) => p.type === "weekday")?.value?.toLowerCase() ?? "";
    const hrStr = parts.find((p) => p.type === "hour")?.value ?? "0";
    const minStr = parts.find((p) => p.type === "minute")?.value ?? "0";
    const wdMap: Record<string, number> = {
      sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6,
    };
    const weekday = wdMap[wdStr.slice(0, 3)];
    if (weekday == null) return null;
    let hour = Number(hrStr);
    if (hour === 24) hour = 0;
    return { weekday, hour: hour + Number(minStr) / 60 };
  } catch {
    return null;
  }
}

export function isWithinBusinessHoursNow(text: string | null | undefined): boolean | null {
  if (!text) return null;
  // Use the first segment if comma-separated ("Mon-Thu 7a-5p, Fri 7a-3p ET").
  // That's a simplification; backend handles full multi-window parsing.
  const segment = text.split(/[,;]/)[0] ?? text;
  const cleaned = segment.replace(/\s+/g, " ").trim();

  // Pull a trailing timezone token if present (last whitespace-separated word).
  const tzMatch = cleaned.match(/\s([A-Za-z/]+)\s*$/);
  let tz = "America/New_York"; // default to ET — matches most US pharma data
  let body = cleaned;
  if (tzMatch) {
    const candidate = tzMatch[1].toUpperCase();
    if (TZ_MAP[candidate]) {
      tz = TZ_MAP[candidate];
      body = cleaned.slice(0, tzMatch.index).trim();
    } else if (candidate.includes("/")) {
      // already an IANA-style zone
      tz = tzMatch[1];
      body = cleaned.slice(0, tzMatch.index).trim();
    }
  }

  // Body shape: "<weekdays> <open>-<close>" e.g. "Mon-Fri 8a-5p"
  const m = /^([A-Za-z]+(?:\s*[-–]\s*[A-Za-z]+)?)\s+(\S+?)\s*[-–]\s*(\S+)$/.exec(body);
  if (!m) return null;
  const weekdays = parseWeekdays(m[1]);
  let openH = parseTimeToken(m[2]);
  let closeH = parseTimeToken(m[3]);
  if (!weekdays || openH == null || closeH == null) return null;

  // Common shorthand "9-5" → 9am-5pm.
  if (openH > 12 === false && closeH < openH) {
    closeH += 12;
  }

  const now = nowInZone(tz);
  if (!now) return null;
  if (!weekdays.has(now.weekday)) return false;
  return now.hour >= openH && now.hour <= closeH;
}
