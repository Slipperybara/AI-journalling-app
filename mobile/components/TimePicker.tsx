import DateTimePicker, { type DateTimePickerEvent } from '@react-native-community/datetimepicker';
import { Platform, View } from 'react-native';

import { clampToFloor } from '../lib/notificationPrefs';

// Inline time picker (iOS spinner) bound to an (hour, minute) pair. Selections
// below the 06:15 floor snap back up so a user can never pick a time the brief
// won't be ready for.
export function TimePicker({
  hour,
  minute,
  onChange,
}: {
  hour: number;
  minute: number;
  onChange: (hour: number, minute: number) => void;
}) {
  const value = new Date();
  value.setHours(hour, minute, 0, 0);

  const handle = (_e: DateTimePickerEvent, d?: Date) => {
    if (!d) return;
    const c = clampToFloor(d.getHours(), d.getMinutes());
    onChange(c.hour, c.minute);
  };

  return (
    <View style={{ alignItems: 'center' }}>
      <DateTimePicker
        value={value}
        mode="time"
        display={Platform.OS === 'ios' ? 'spinner' : 'default'}
        minuteInterval={5}
        onChange={handle}
      />
    </View>
  );
}
