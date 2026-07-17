function canMergeTextBlockDelta(
	previous: Record<string, unknown>,
	current: Record<string, unknown>,
): boolean {
	if (previous.type !== 'TEXT_BLOCK_DELTA' || current.type !== 'TEXT_BLOCK_DELTA') {
		return false;
	}
	return previous.reply_id === current.reply_id && previous.block_id === current.block_id;
}

function mergeTextBlockDelta(
	previous: Record<string, unknown>,
	current: Record<string, unknown>,
): Record<string, unknown> {
	const merged = { ...previous };
	if (typeof previous.delta === 'string' && typeof current.delta === 'string') {
		merged.delta = `${previous.delta}${current.delta}`;
	}
	merged.merged_count = Number(previous.merged_count ?? 1) + 1;
	return merged;
}

export function mergeAdjacentTextBlockDeltas(
	events: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
	const merged: Array<Record<string, unknown>> = [];
	for (const event of events) {
		const previous = merged[merged.length - 1];
		if (previous && canMergeTextBlockDelta(previous, event)) {
			merged[merged.length - 1] = mergeTextBlockDelta(previous, event);
		} else {
			merged.push(event);
		}
	}
	return merged;
}
