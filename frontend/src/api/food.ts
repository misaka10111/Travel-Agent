import type { FoodSearchMeta, Restaurant } from './types';

/** A failed food query is an error object, rather than a restaurant array. */
export function getFoodRestaurants(search: Record<string, unknown> | null): Restaurant[] {
  if (!Array.isArray(search?.food)) return [];
  return search.food.filter((item): item is Restaurant => (
    item !== null && typeof item === 'object'
    && typeof item.id === 'string' && typeof item.name === 'string'
    && typeof item.url === 'string'
  ));
}

function normalizedName(value: string): string {
  return value.normalize('NFKC').replace(/\s+/g, '').toLowerCase();
}

/** Only display facts when the block identifies one restaurant unambiguously. */
export function findRestaurantForBlock(blockName: string, food: Restaurant[]): Restaurant | undefined {
  const target = normalizedName(blockName);
  if (!target) return undefined;
  const exact = food.filter((item) => normalizedName(item.name) === target);
  if (exact.length > 0) return exact.length === 1 ? exact[0] : undefined;

  const prefixes = food.filter((item) => {
    const name = normalizedName(item.name);
    if (!name || !target.startsWith(name)) return false;
    // A meal label is safe to remove; an unrecognized suffix may name another branch.
    const suffix = target.slice(name.length);
    return /^(?:[-—·:](?:早餐|午餐|晚餐|早饭|午饭|晚饭|下午茶|夜宵|宵夜|用餐|就餐)|\((?:早餐|午餐|晚餐|早饭|午饭|晚饭|下午茶|夜宵|宵夜|用餐|就餐)\))$/.test(suffix);
  });
  if (prefixes.length > 0) {
    const longest = Math.max(...prefixes.map((item) => normalizedName(item.name).length));
    const matches = prefixes.filter((item) => normalizedName(item.name).length === longest);
    return matches.length === 1 ? matches[0] : undefined;
  }
  return undefined;
}

/** Local edits retain the facts for untouched meals and update restaurants by POI ID. */
export function mergeSearchData(
  previous: Record<string, unknown> | null,
  incoming: Record<string, unknown> | null,
): Record<string, unknown> | null {
  if (!incoming) return previous;
  if (!previous) return incoming;
  const result = { ...previous, ...incoming };
  const oldFood = getFoodRestaurants(previous);
  if (Array.isArray(incoming.food)) {
    const byId = new Map(oldFood.map((item) => [item.id, item]));
    for (const item of getFoodRestaurants(incoming)) byId.set(item.id, item);
    result.food = [...byId.values()];
  } else if (incoming.food && typeof incoming.food === 'object' && oldFood.length > 0) {
    // Preserve untouched meals if the new search fails, while showing that failure.
    const error = (incoming.food as { error?: unknown }).error;
    const message = typeof error === 'string' ? error
      : error && typeof error === 'object' && 'message' in error ? String(error.message) : '';
    if (message) {
      const metadata = (incoming.food_meta ?? previous.food_meta ?? {}) as FoodSearchMeta;
      result.food = oldFood;
      result.food_meta = {
        ...metadata,
        warnings: [...(metadata.warnings ?? []), `餐饮查询失败：${message}`],
      };
    }
  }
  return result;
}
