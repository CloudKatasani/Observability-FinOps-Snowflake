// Exact decimal arithmetic on the strings the API sends.
//
// Credits and currency cross the API boundary as strings precisely so that no
// float ever touches them (§27.7). Parsing them with `parseFloat` to display
// them would reintroduce the error the backend went to trouble to avoid, so
// every figure the user reads is produced by the functions below, which work on
// BigInt digits and an explicit scale.
//
// `toPlotNumber` is the one deliberate exception: ECharts plots doubles, and a
// pixel position does not need cent precision. It is named so that its use is
// visible in review.

export interface Decimal {
  /** True when the value is strictly negative. Zero is never negative. */
  readonly neg: boolean;
  /** Absolute value as an integer, i.e. |value| * 10^scale. */
  readonly unscaled: bigint;
  /** Number of implied decimal places. Never negative. */
  readonly scale: number;
}

/** Plain decimal, optionally signed, optionally in scientific notation. */
const DECIMAL_PATTERN = /^([+-])?(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/;

export const ZERO: Decimal = { neg: false, unscaled: 0n, scale: 0 };

function pow10(exponent: number): bigint {
  return 10n ** BigInt(exponent);
}

/**
 * Parse an API figure. Returns null for anything that is not a number —
 * including null itself, which R3 requires we render as "unknown", never 0.
 */
export function parseDecimal(raw: unknown): Decimal | null {
  if (raw === null || raw === undefined || typeof raw === "boolean") {
    return null;
  }
  if (typeof raw === "number") {
    if (!Number.isFinite(raw)) return null;
    return parseDecimal(raw.toString());
  }
  if (typeof raw !== "string") return null;

  const match = DECIMAL_PATTERN.exec(raw.trim());
  if (!match) return null;

  const [, sign, integerPart, fractionPart = "", exponentPart] = match;
  let scale = fractionPart.length;
  if (exponentPart) {
    scale -= Number(exponentPart);
  }
  let unscaled = BigInt(`${integerPart}${fractionPart}`);
  if (scale < 0) {
    unscaled *= pow10(-scale);
    scale = 0;
  }
  return { neg: sign === "-" && unscaled !== 0n, unscaled, scale };
}

/** Restate a value at a different scale, rounding half away from zero. */
export function rescale(value: Decimal, scale: number): Decimal {
  if (scale === value.scale) return value;
  if (scale > value.scale) {
    return { ...value, unscaled: value.unscaled * pow10(scale - value.scale), scale };
  }
  const factor = pow10(value.scale - scale);
  const quotient = value.unscaled / factor;
  const remainder = value.unscaled % factor;
  const unscaled = remainder * 2n >= factor ? quotient + 1n : quotient;
  return { neg: value.neg && unscaled !== 0n, unscaled, scale };
}

function signedUnscaled(value: Decimal): bigint {
  return value.neg ? -value.unscaled : value.unscaled;
}

export function addDecimals(a: Decimal, b: Decimal): Decimal {
  const scale = Math.max(a.scale, b.scale);
  const total = signedUnscaled(rescale(a, scale)) + signedUnscaled(rescale(b, scale));
  return { neg: total < 0n, unscaled: total < 0n ? -total : total, scale };
}

/** Sum a column of API figures exactly. Unparseable entries are skipped. */
export function sumDecimals(values: readonly unknown[]): Decimal {
  let total = ZERO;
  for (const value of values) {
    const parsed = parseDecimal(value);
    if (parsed) total = addDecimals(total, parsed);
  }
  return total;
}

export function compareDecimals(a: Decimal, b: Decimal): number {
  const scale = Math.max(a.scale, b.scale);
  const left = signedUnscaled(rescale(a, scale));
  const right = signedUnscaled(rescale(b, scale));
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

export function isZero(value: Decimal): boolean {
  return value.unscaled === 0n;
}

/**
 * Move the decimal point right by `places` — an exact ×10^places.
 * Used to render a stored fraction as a percentage without dividing.
 */
export function shiftPoint(value: Decimal, places: number): Decimal {
  const scale = value.scale - places;
  if (scale >= 0) return { ...value, scale };
  return { ...value, unscaled: value.unscaled * pow10(-scale), scale: 0 };
}

/** Exact division, rounded half away from zero at `scale` decimal places. */
export function divideDecimals(a: Decimal, b: Decimal, scale: number): Decimal | null {
  if (b.unscaled === 0n) return null;
  const exponent = scale + b.scale - a.scale + 1;
  let numerator = a.unscaled;
  let denominator = b.unscaled;
  if (exponent >= 0) {
    numerator *= pow10(exponent);
  } else {
    denominator *= pow10(-exponent);
  }
  const extended = numerator / denominator;
  const lastDigit = extended % 10n;
  let unscaled = extended / 10n;
  if (lastDigit >= 5n) unscaled += 1n;
  return { neg: a.neg !== b.neg && unscaled !== 0n, unscaled, scale };
}

/** Fixed-point text with no thousands separators, e.g. "-1234.50". */
export function toFixed(value: Decimal, decimals: number): string {
  const scaled = rescale(value, decimals);
  const digits = scaled.unscaled.toString().padStart(decimals + 1, "0");
  const integerPart = decimals > 0 ? digits.slice(0, -decimals) : digits;
  const fractionPart = decimals > 0 ? digits.slice(-decimals) : "";
  const sign = scaled.neg ? "-" : "";
  return fractionPart ? `${sign}${integerPart}.${fractionPart}` : `${sign}${integerPart}`;
}

/**
 * Convert to a double **for chart plotting only**. Never use the result in a
 * figure the user reads — format the string instead.
 */
export function toPlotNumber(value: Decimal): number {
  return Number(toFixed(value, value.scale));
}
