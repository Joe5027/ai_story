"""8 至 10 秒目标镜头的原生时长分段、续帧与精确合成规划。"""

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Dict, List


TIME_QUANTUM = Decimal('0.001')


def _seconds(value: Any, field_name: str = '时长') -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{field_name}必须是有效数字。')
    if result <= 0:
        raise ValueError(f'{field_name}必须大于 0。')
    return result


def plan_video_segments(
    target_duration_seconds: Any,
    max_native_duration: Any = 5,
    fps: int = 24,
    overlap_frames: int = 8,
    min_target_duration: Any = 8,
    max_target_duration: Any = 10,
) -> List[Dict[str, Any]]:
    """规划原生视频调用，并保证交叉淡化后的输出精确等于目标时长。

    后续段以 ``previous_final_frame`` 为输入。每个后续段包含固定帧数的
    crossfade 重叠，因此原生请求总时长等于目标时长加重叠时长；最后由
    ``trim_exact_duration`` 约束合成器精确裁到目标时长。
    """

    target = _seconds(target_duration_seconds, '目标时长')
    native_limit = _seconds(max_native_duration, '原生时长上限')
    minimum = _seconds(min_target_duration, '目标最小时长')
    maximum = _seconds(max_target_duration, '目标最大时长')
    if minimum > maximum:
        raise ValueError('目标最小时长不能大于最大时长。')
    if target < minimum or target > maximum:
        raise ValueError(f'目标镜头总长必须位于 {minimum} 至 {maximum} 秒。')
    if fps < 1:
        raise ValueError('fps 必须大于 0。')
    if overlap_frames < 0:
        raise ValueError('overlap_frames 不能小于 0。')

    overlap = (Decimal(overlap_frames) / Decimal(fps)).quantize(
        TIME_QUANTUM, rounding=ROUND_HALF_UP
    )
    if overlap >= native_limit:
        raise ValueError('重叠时长必须小于单段原生时长上限。')

    if target <= native_limit:
        segment_count = 1
    else:
        effective_per_additional_segment = native_limit - overlap
        segment_count = int(
            ((target - overlap) / effective_per_additional_segment).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        segment_count = max(2, segment_count)

    raw_total = target + overlap * Decimal(segment_count - 1)
    if segment_count == 2 and raw_total - native_limit > overlap:
        raw_durations = [native_limit, raw_total - native_limit]
    else:
        even = raw_total / Decimal(segment_count)
        raw_durations = [even] * segment_count

    segments: List[Dict[str, Any]] = []
    output_cursor = Decimal('0')
    allocated_raw = Decimal('0')
    for index in range(segment_count):
        if index == segment_count - 1:
            raw_duration = raw_total - allocated_raw
        else:
            raw_duration = raw_durations[index].quantize(TIME_QUANTUM, rounding=ROUND_HALF_UP)
        allocated_raw += raw_duration
        segment_overlap = Decimal('0') if index == 0 else overlap
        output_start = max(Decimal('0'), output_cursor - segment_overlap)
        if index == segment_count - 1:
            output_end = target
            raw_duration = output_end - output_start
        else:
            output_end = output_start + raw_duration
        effective_duration = output_end - output_cursor
        previous_final_frame = None
        if index > 0:
            previous_final_frame = {
                'segment_index': index - 1,
                'frame_role': 'final',
            }
        is_last = index == segment_count - 1
        segments.append(
            {
                'index': index,
                'start_seconds': output_start,
                'end_seconds': output_end,
                'duration_seconds': raw_duration,
                'requested_duration_seconds': raw_duration,
                'effective_duration_seconds': effective_duration,
                'previous_final_frame': previous_final_frame,
                'crossfade': {
                    'enabled': index > 0 and overlap_frames > 0,
                    'frames': overlap_frames if index > 0 else 0,
                    'duration_seconds': segment_overlap,
                },
                'trim_exact_duration': {
                    'enabled': is_last,
                    'target_duration_seconds': target if is_last else None,
                    'output_end_seconds': output_end if is_last else None,
                },
            }
        )
        output_cursor = output_end

    if any(item['requested_duration_seconds'] > native_limit for item in segments):
        raise ValueError('分段规划超过原生时长上限。')
    return segments
