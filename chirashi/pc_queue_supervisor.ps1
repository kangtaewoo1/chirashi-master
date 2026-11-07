$ErrorActionPreference='Stop'
$queueRoot='C:\Users\a\Desktop\chirashi-master-master\chirashi'
$queueMutex=New-Object System.Threading.Mutex($false,'Local\ChirashiPCQueueSupervisor')
if(-not $queueMutex.WaitOne(0)){exit}
try {
 while($true){
  $running=Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object {$_.CommandLine -match 'pc_node.py|pc_queue.py'}
  if(-not $running){
   $proc=Start-Process -FilePath 'C:\Users\a\AppData\Local\Programs\Python\Python313\python.exe' -ArgumentList @('pc_node.py') -WorkingDirectory $queueRoot -WindowStyle Hidden -RedirectStandardOutput "$queueRoot\data\pc-queue.stdout.log" -RedirectStandardError "$queueRoot\data\pc-queue.stderr.log" -PassThru
   $proc.WaitForExit()
  }
  Start-Sleep -Seconds 15
 }
} finally {$queueMutex.ReleaseMutex();$queueMutex.Dispose()}
