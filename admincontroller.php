<?php

namespace App\Http\Controllers;
use App\Http\Controllers\Controller;
use Illuminate\Support\Facades\DB;
use Illuminate\Http\Request;

class adminController extends Controller
{
    
 public function login(Request $request){ 
     
    $admin=DB::table('users')->join('admin','users.id','=','admin.users_id')->where('users.full_name',$request->full_name)
     ->where('users.national_id',$request->national_id)
     ->where('admin.code',$request->code)->first();
    if($admin){
        return response()->json(['message'=>'Login success'],200);       
    }
    
      return response()->json(['message'=>'invalid  data'],401);
 }


  public function add(Request $request){ 
       
       
       $cv=time().'_'.
       $request->description->getClientOriginalName();
       $request->description->move(public_path('uploads'),$cv);
        $symbol_img=time().'_'.
       $request->symbol_img->getClientOriginalName();
       $request->symbol_img->move(public_path('uploads'),$symbol_img);
       $img=time().'_'.
       $request->img->getClientOriginalName();
       $request->img->move(public_path('uploads'),$img);
       $user=DB::table('users')->where('full_name',$request->full_name)
             ->where('national_id',$request->national_id)->first(); 
              if(!$user){
           return response()->json(['message'=>'The entered data is incorrect '],401);  
           }else{
          DB::table('candidates')->insert(['users_id'=>$user->id,'job'=>$request->job,'symbol'=>$request->symbol,
           'description'=>$cv,
           'symbol_img'=>$symbol_img,
           'img'=>$img,
           'type'=>"فردى"]);
          DB::table('users')->where('id',$user->id)->update(['role'=>'candidate']);
        
           return response()->json(['message'=>'Login success'],200); 
            }
 }

public function update(Request $request) {
    $id = $request->id;

    $candidate = DB::table('candidates')->where('id', $id)->first();

    $data = [];

    if ($request->has('job') && $request->job != $candidate->job) {
        $data['job'] = $request->job;
    }

    if ($request->has('symbol') && $request->symbol != $candidate->symbol) {
        $data['symbol'] = $request->symbol;
    }

    if ($request->hasFile('description')) {
        $cv = time() . '_' . $request->description->getClientOriginalName();
        $request->description->move(public_path('uploads'), $cv);
        if ($cv != $candidate->description) {
            $data['description'] = $cv;
        }
    }

    if ($request->hasFile('symbol_img')) {
        $symbol_img = time() . '_' . $request->symbol_img->getClientOriginalName();
        $request->symbol_img->move(public_path('uploads'), $symbol_img);
        if ($symbol_img != $candidate->symbol_img) {
            $data['symbol_img'] = $symbol_img;
        }
    }

    if ($request->hasFile('img')) {
        $img = time() . '_' . $request->img->getClientOriginalName();
        $request->img->move(public_path('uploads'), $img);
        if ($img != $candidate->img) {
            $data['img'] = $img;
        }
    }

    if (!empty($data)) {
        DB::table('candidats')->where('id', $id)->update($data);
    }

    return response()->json(['message' => 'Updated successfully']);
}
    // public function delete(Request $request){

    // $ids=explode(',',$request->ids);
    // $candidates=DB::table('candidates')->whereIn('id',$ids)->first();
    // foreach($candidates as $candidates):
    // DB::table('users')->where('id',$candidates->users_id)->update(['role'=>null]);
    // DB::table('candidats')->where('id',$ids)->delete();
    // endforeach;
    // return response()->json(['message'=>'delete successfully']);

    // }
    public function delete(Request $request)
{
    
    $rawIds=$request->ids;
    if (is_string($rawIds)) {
        // شيل الأقواس وأي مسافات، وبعدين قطع النص عند الفاصلة
    $cleanString = trim($rawIds, '[]'); 
    $ids = array_map('trim', explode(',', $cleanString));
    } else {
        $ids = $rawIds;
    }

    // التأكد إن الـ ids بقت أرقام فقط (لضمان إن أول عنصر يتقرأ صح)
    $ids = array_filter($ids, 'is_numeric');

    $candidates = DB::table('candidates')->whereIn('id', $ids)->get();

    $userIds = $candidates->pluck('users_id');

    DB::table('users')->whereIn('id', $userIds)->update(['role' => null]);

    DB::table('candidates')->whereIn('id', $ids)->delete();

    return response()->json(['message' => 'Deleted successfully']);
}
    
        public function date(Request $request){
     $election=DB::table('elections')->latest('id')->first();
     if ($election != null){
        DB::table('elections')->where('id',$election->id)->update([
      'date_start'=>$request->date_start,
       'date_end'=>$request->date_end ]);
     }else{
     DB::table('elections')->insert([
      'date_start'=>$request->date_start,
       'date_end'=>$request->date_end]);
     }
     

    
   
    return response()->json(['message'=>'save successfully']);

    }
    public function store_inst(Request $request){
    $election=DB::table('elections')->latest('id')->first();
   
     DB::table('elections')->where('id',$election->id)->update([
      'inst_indiv'=>$request->inst_indiv

     ]);
   
    return response()->json(['message'=>'save successfully']);

    }
}