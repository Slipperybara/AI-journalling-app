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

  const width = fill.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] });

  return (
    <Pressable
      onPressIn={start}
      onPressOut={cancel}
      style={{
        height: 56,
        borderRadius: 18,
        backgroundColor: '#2A2825',
        overflow: 'hidden',
        justifyContent: 'center',
        alignItems: 'center',
      }}
    >
      <Animated.View
        style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width, backgroundColor: '#6E9B7A' }}
      />
      <Text style={{ fontFamily: fonts.sansMedium, fontSize: 16, color: '#fff' }}>{label}</Text>
    </Pressable>
  );
}
