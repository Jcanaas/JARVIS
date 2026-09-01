<script>
  import StreamingBrowser from '../lib/StreamingBrowser.svelte'
  let { active = true } = $props()
</script>

<StreamingBrowser kind="tv" label="TV" {active} />
