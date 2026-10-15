// The API uses Unicode code points, while indexOf returns UTF-16 positions.
export function sourceQuoteRange(text: string, quote: string) {
  const index = text.indexOf(quote);
  if (!quote || index < 0) return null;
  const start = Array.from(text.slice(0, index)).length;
  return { start, end: start + Array.from(quote).length };
}

export function splitSourceQuote(text: string, start: number, end: number) {
  const points = Array.from(text);
  return { before: points.slice(0, start).join(""), quote: points.slice(start, end).join(""), after: points.slice(end).join("") };
}
