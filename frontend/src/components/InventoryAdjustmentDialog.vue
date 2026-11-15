<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { adjustmentReasons, newIdempotencyKey, products } from '../api'
import SelectMenu from './SelectMenu.vue'

const props = defineProps({
  productId: { type: [String, Number], required: true },
  variant: { type: Object, required: true },
  inventory: { type: Object, required: true },
})
const emit = defineEmits(['saved', 'close'])

const dialog = ref(null)
const type = ref('increase')
const quantity = ref('')
const reason = ref('')
const note = ref('')
const saving = ref(false)
const error = ref('')
const reasonMenuOpen = ref(false)
// One key for this popup. Saving again after a lost reply repeats the same
// request, so the server applies it once.
const idempotencyKey = newIdempotencyKey()

const reasons = computed(() => adjustmentReasons[type.value])
const amount = computed(() => Number(quantity.value))
const validAmount = computed(() => quantity.value !== '' && Number.isInteger(amount.value) && amount.value >= 1)
const newQuantity = computed(() => {
  if (!validAmount.value) return null
  return type.value === 'increase' ? props.inventory.quantity + amount.value : props.inventory.quantity - amount.value
})
const belowReserved = computed(() => newQuantity.value !== null && newQuantity.value < props.inventory.reserved)

// A reason picked for one type may not exist for the other.
watch(type, () => {
  if (!reasons.value.some((item) => item.value === reason.value)) reason.value = ''
})

onMounted(() => dialog.value.showModal())

// Keep digits only, so a minus sign, a decimal point or a letter cannot be entered.
function onQuantityInput(event) {
  quantity.value = event.target.value.replace(/\D/g, '')
  event.target.value = quantity.value
}

function validate() {
  if (!validAmount.value) return 'Enter a whole number of 1 or more.'
  if (!reason.value) return 'Choose a reason.'
  if (reason.value === 'other' && !note.value.trim()) return 'Add a note to explain the reason.'
  if (belowReserved.value) {
    return 'Stock cannot go below the reserved quantity (reserved ' + props.inventory.reserved +
      ', in stock ' + props.inventory.quantity + ').'
  }
  return ''
}

async function save() {
  if (saving.value) return
  error.value = validate()
  if (error.value) return
  saving.value = true
  try {
    const result = await products.adjustInventory(props.productId, props.variant.id, {
      type: type.value,
      quantity: amount.value,
      reason: reason.value,
      note: note.value.trim(),
    }, idempotencyKey)
    emit('saved', result)
    dialog.value.close()
  } catch (failure) {
    error.value = failure.message
  } finally {
    saving.value = false
  }
}

function cancel() {
  if (!saving.value) dialog.value.close()
}

// Escape must not close the popup while a save is in flight, or while it is
// only closing the reason menu.
function onCancel(event) {
  if (saving.value || reasonMenuOpen.value) event.preventDefault()
}
</script>

<template>
  <dialog ref="dialog" class="dialog" aria-labelledby="adjust-title" @cancel="onCancel" @close="emit('close')">
    <form novalidate @submit.prevent="save" @input="error = ''">
      <header class="dialog-header">
        <h2 id="adjust-title">Adjust stock</h2>
        <p>{{ variant.name }} <span class="code">· {{ variant.sku }}</span></p>
      </header>

      <fieldset class="dialog-body" :disabled="saving">
        <dl class="stock-summary">
          <div><dt>In stock</dt><dd>{{ inventory.quantity }}</dd></div>
          <div><dt>Reserved</dt><dd>{{ inventory.reserved }}</dd></div>
          <div><dt>Available</dt><dd>{{ inventory.quantity - inventory.reserved }}</dd></div>
        </dl>

        <fieldset class="choice-group">
          <legend>Adjustment type</legend>
          <label><input v-model="type" type="radio" value="increase" /> Increase</label>
          <label><input v-model="type" type="radio" value="decrease" /> Decrease</label>
        </fieldset>

        <label class="field">Quantity
          <input :value="quantity" type="text" inputmode="numeric" autocomplete="off" maxlength="10" required @input="onQuantityInput" />
        </label>

        <div class="field">
          <span id="adjust-reason-label">Reason</span>
          <SelectMenu v-model="reason" v-model:open="reasonMenuOpen" name="adjust-reason" labelled-by="adjust-reason-label"
            :options="reasons" placeholder="Choose a reason" @update:model-value="error = ''" />
        </div>

        <label class="field">Note <small>{{ reason === 'other' ? 'Required for Other' : 'Optional' }}</small>
          <input v-model="note" maxlength="255" :required="reason === 'other'" />
        </label>

        <p class="stock-preview" role="status">
          <template v-if="newQuantity !== null && !belowReserved">New quantity: <strong>{{ newQuantity }}</strong></template>
          <template v-else-if="belowReserved">This would take stock below the reserved quantity.</template>
          <template v-else>Enter a quantity to see the new stock level.</template>
        </p>
      </fieldset>

      <footer class="form-footer">
        <p v-if="error" class="error-text" role="alert">{{ error }}</p>
        <div class="dialog-actions">
          <button class="button" type="button" :disabled="saving" @click="cancel">Cancel</button>
          <button class="button button-primary" type="submit" :disabled="saving">
            {{ saving ? 'Saving...' : 'Save adjustment' }}
          </button>
        </div>
      </footer>
    </form>
  </dialog>
</template>
