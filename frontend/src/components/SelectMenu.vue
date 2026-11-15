<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

// A dropdown drawn by the app, so the open menu matches the rest of the UI.
// Follows the "select-only combobox" keyboard pattern: arrows move, Enter or
// Space chooses, Escape closes.
const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, required: true },
  placeholder: { type: String, default: 'Choose' },
  labelledBy: { type: String, required: true },
  name: { type: String, required: true },
})
const emit = defineEmits(['update:modelValue', 'update:open'])

const root = ref(null)
const list = ref(null)
const open = ref(false)
const active = ref(0)
const selected = computed(() => props.options.find((option) => option.value === props.modelValue))

function optionId(index) {
  return props.name + '-option-' + index
}

function show() {
  const index = props.options.findIndex((option) => option.value === props.modelValue)
  active.value = index === -1 ? 0 : index
  open.value = true
}

function hide() {
  open.value = false
}

function choose(index) {
  emit('update:modelValue', props.options[index].value)
  hide()
}

function move(step) {
  const count = props.options.length
  active.value = (active.value + step + count) % count
}

function onKeydown(event) {
  if (!open.value) {
    if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
      event.preventDefault()
      show()
    }
    return
  }
  switch (event.key) {
    case 'ArrowDown': move(1); break
    case 'ArrowUp': move(-1); break
    case 'Home': active.value = 0; break
    case 'End': active.value = props.options.length - 1; break
    case 'Enter':
    case ' ': choose(active.value); break
    case 'Escape': hide(); break
    case 'Tab': hide(); return
    default: return
  }
  // Stops Escape from also closing a popup this menu sits in.
  event.preventDefault()
  event.stopPropagation()
}

function onOutsidePress(event) {
  if (!root.value.contains(event.target)) hide()
}

watch(open, async (isOpen) => {
  emit('update:open', isOpen)
  if (isOpen) {
    document.addEventListener('pointerdown', onOutsidePress)
    await nextTick()
    list.value?.children[active.value]?.scrollIntoView({ block: 'nearest' })
  } else {
    document.removeEventListener('pointerdown', onOutsidePress)
  }
})

onBeforeUnmount(() => document.removeEventListener('pointerdown', onOutsidePress))
</script>

<template>
  <div ref="root" class="select-menu">
    <button class="select-menu-button" type="button" role="combobox" aria-haspopup="listbox"
      :aria-expanded="open" :aria-controls="name + '-list'" :aria-labelledby="labelledBy + ' ' + name + '-value'"
      :aria-activedescendant="open ? optionId(active) : null" @click="open ? hide() : show()" @keydown="onKeydown">
      <span :id="name + '-value'" :class="{ 'select-menu-placeholder': !selected }">{{ selected ? selected.label : placeholder }}</span>
      <span class="select-menu-arrow" aria-hidden="true"></span>
    </button>
    <ul v-show="open" :id="name + '-list'" ref="list" class="select-menu-list" role="listbox" :aria-labelledby="labelledBy">
      <li v-for="(option, index) in options" :id="optionId(index)" :key="option.value" role="option"
        :aria-selected="option.value === modelValue" :class="{ 'is-active': index === active }"
        @pointerenter="active = index" @click="choose(index)">
        {{ option.label }}
      </li>
    </ul>
  </div>
</template>
