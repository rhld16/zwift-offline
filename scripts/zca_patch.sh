bundleId="com.zwift.android.prod"
apk=`adb shell pm path $bundleId`
apk=`echo $apk | awk '{print $NF}' FS=':' | tr -d '\r\n'`
adb pull $apk zca.apk
apk-mitm --certificate ../ssl/cert-zwift-com.pem zca.apk
