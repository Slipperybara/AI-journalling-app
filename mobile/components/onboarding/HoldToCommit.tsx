import { useRef } from 'react';
import { Animated, Pressable, Text } from 'react-native';

import { fonts } from '../../lib/theme';

// Press-and-hold button: a positivity bar fills over 3s; releasing early cancels.
export function HoldToCommit({ label, onComplete }: { label: string; onComplete: () => void }) {
  const fill = useRef(new Animated.Value(0)).current;
  const anim = useRef<Animated.CompositeAnimation | null>(null);
  const done = useRef(false);

  const start = () => {
    done.current = false;
    anim.current = Animated.timing(fill, { toValue: 1, duration: 3000, useNativeDriver: false });
    anim.current.start(({ finished }) => {
      if (finished && !done.current) {
        done.current = true;
        onComplete();
      }
    });
  };

  const cancel = () => {
    anim.current?.stop();
    if (!done.current) {
      Animated.timing(fill, { toValue: 0, duration: 220, useNativeDriver: false }).start();
    }
  };

  // A circular "fills up" metaphor: a deeper amber rises from the bottom as the
  // hold completes. Releasing early drains it back.
  const SIZE = 180;
  const height = fill.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] });

  return (
    <Pressable
      onPressIn={start}
      onPressOut={cancel}
      style={{
        width: SIZE,
        height: SIZE,
        borderRadius: SIZE / 2,
        backgroundColor: '#F0C84B',
        overflow: 'hidden',
        justifyContent: 'center',
        alignItems: 'center',
        shadowColor: '#000',
        shadowOpacity: 0.12,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 6 },
      }}
    >
      <Animated.View
        style={{ position: 'absolute', left: 0, right: 0, bottom: 0, height, backgroundColor: '#E0A21F' }}
      />
      <Text style={{ fontFamily: fonts.sansMedium, fontSize: 17, color: '#2A2825', textAlign: 'center' }}>
        {label}
      </Text>
    </Pressable>
  );
}
