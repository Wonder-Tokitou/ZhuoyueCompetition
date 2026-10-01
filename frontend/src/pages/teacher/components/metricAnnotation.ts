export function metricAnnotation(value: string) {
  const match = value.match(/^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*[%％])\s*([\s\S]*)$/)
  return match ? { value: match[1].trim(), note: match[2].trim() } : { value, note: '' }
}
