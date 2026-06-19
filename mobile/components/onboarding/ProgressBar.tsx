import { View } from 'react-native';

import { colors } from '../../lib/theme';

// Visual-only progress (no numbers/words). `progress` is 0..1.
export function ProgressBar({ progress }: { progress: number }) {
  const pct = Math.max(0, Math.min(1, progress)) * 100;
  return (
    <View style={{ height: 4, borderRadius: 2, backgroundColor: '#E0DCD4', overflow: 'hidden' }}>
      <View style={{ width: `${pct}%`, height: '100%', borderRadius: 2, backgroundColor: colors.ink }} />
    </View>
  );
}
