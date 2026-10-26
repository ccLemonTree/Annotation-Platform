export async function imageBatches<T>(ids: string[], send: (batch: string[]) => Promise<T>, completed?: (ids: string[]) => void): Promise<T[]> {
  const results: T[] = [];
  for (let offset = 0; offset < ids.length; offset += 1000) {
    const batch = ids.slice(offset, offset + 1000);
    results.push(await send(batch));
    completed?.(batch);
  }
  return results;
}
