/** Nearby hospital lookup from a lat/lng (OpenStreetMap Overpass, Nominatim fallback). */

export interface NearbyHospital {
  name: string;
  meters: number;
}

export const HOSPITAL_RADIUS_M = 400;
const USER_AGENT = "Amyna-Relay/0.1 (https://github.com/tiwari-ira/amyna)";

export function metersBetween(
  a: { latitude: number; longitude: number },
  b: { latitude: number; longitude: number },
): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.min(1, Math.sqrt(h)));
}

const HOSPITALISH = /\b(hospital|medical center|clinic|urgent care|emergency)\b/i;

export function looksLikeHospitalName(name: string | undefined): boolean {
  return Boolean(name && HOSPITALISH.test(name));
}

type FetchLike = typeof fetch;

async function overpass(
  latitude: number,
  longitude: number,
  radiusM: number,
  fetchImpl: FetchLike,
): Promise<NearbyHospital | undefined> {
  const query =
    `[out:json][timeout:8];` +
    `(nwr["amenity"~"^(hospital|clinic)$"](around:${radiusM},${latitude},${longitude});` +
    `nwr["healthcare"~"^(hospital|clinic)$"](around:${radiusM},${latitude},${longitude}););` +
    `out center 5;`;
  const response = await fetchImpl("https://overpass-api.de/api/interpreter", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
      "User-Agent": USER_AGENT,
    },
    body: `data=${encodeURIComponent(query)}`,
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) return undefined;
  const body = (await response.json()) as {
    elements?: {
      lat?: number;
      lon?: number;
      center?: { lat: number; lon: number };
      tags?: { name?: string };
    }[];
  };
  let best: NearbyHospital | undefined;
  for (const element of body.elements ?? []) {
    const lat = element.lat ?? element.center?.lat;
    const lon = element.lon ?? element.center?.lon;
    const name = element.tags?.name?.trim();
    if (lat === undefined || lon === undefined || !name) continue;
    const meters = metersBetween({ latitude, longitude }, { latitude: lat, longitude: lon });
    if (meters > radiusM) continue;
    if (!best || meters < best.meters) best = { name, meters };
  }
  return best;
}

async function nominatim(
  latitude: number,
  longitude: number,
  fetchImpl: FetchLike,
): Promise<NearbyHospital | undefined> {
  const url =
    `https://nominatim.openstreetmap.org/reverse?lat=${latitude}&lon=${longitude}` +
    `&format=json&zoom=18&addressdetails=1`;
  const response = await fetchImpl(url, {
    headers: { "User-Agent": USER_AGENT, Accept: "application/json" },
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) return undefined;
  const body = (await response.json()) as {
    name?: string;
    display_name?: string;
    type?: string;
    class?: string;
    address?: { hospital?: string; clinic?: string; amenity?: string };
  };
  const name =
    body.address?.hospital ?? body.address?.clinic ?? body.name ?? body.display_name?.split(",")[0];
  const amenity = body.address?.amenity ?? body.type ?? "";
  if (!name || (!looksLikeHospitalName(name) && !HOSPITALISH.test(amenity))) return undefined;
  return { name: name.trim(), meters: 0 };
}

/** Closest hospital or clinic within `radiusM`, or undefined when none is found. */
export async function findNearbyHospital(
  latitude: number,
  longitude: number,
  options: { fetchImpl?: FetchLike; radiusM?: number } = {},
): Promise<NearbyHospital | undefined> {
  const fetchImpl = options.fetchImpl ?? fetch;
  const radiusM = options.radiusM ?? HOSPITAL_RADIUS_M;
  try {
    const fromMap = await overpass(latitude, longitude, radiusM, fetchImpl);
    if (fromMap) return fromMap;
  } catch {
    // Nominatim is the fallback.
  }
  try {
    return await nominatim(latitude, longitude, fetchImpl);
  } catch {
    return undefined;
  }
}
