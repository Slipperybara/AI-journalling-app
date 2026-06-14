import { Pressable, ScrollView, Text } from 'react-native';

import { fonts } from '../../lib/theme';

type Props =
  | { multi?: false; options: string[]; value?: string; onChange: (v: string) => void }
  | { multi: true; options: string[]; value: string[]; onChange: (v: string[]) => void };

export function ChoiceGroup(props: Props) {
  const isSelected = (o: string) => (props.multi ? props.value.includes(o) : props.value === o);

  const toggle = (o: string) => {
    if (props.multi) {
      props.onChange(
        props.value.includes(o) ? props.value.filter((x) => x !== o) : [...props.value, o],
      );
    } else {
      props.onChange(o);
    }
  };

  return (
    <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ gap: 12, paddingVertical: 4 }}>
      {props.options.map((o) => {
        const sel = isSelected(o);
        return (
          <Pressable
            key={o}
            onPress={() => toggle(o)}
            style={{
              borderWidth: 1.5,
              borderColor: sel ? '#2A2825' : '#DDD8D0',
              backgroundColor: sel ? 'rgba(42,40,37,0.04)' : 'transparent',
              borderRadius: 16,
              paddingVertical: 16,
              paddingHorizontal: 18,
            }}
          >
            <Text style={{ fontFamily: sel ? fonts.sansMedium : fonts.sans, fontSize: 16, color: '#38342F' }}>
              {o}
            </Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}
